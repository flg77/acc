"""Backlog 019 A3 + A5 + the ConfigIQ port: the GPU capacity advisor's skills.

* ``gpu_capacity_estimate`` / :mod:`acc.gpu_sizing` — vLLM memory sizing,
  checked against hand-computed numbers for well-known model geometries.
* ``prometheus_query`` — read-only PromQL, capped, optional allowlist.
* ``ocp_model_control`` — the one acting skill: namespace allowlist refused
  before any request, every call gated in all modes but PLAN, approve ->
  annotation patched, reject -> nothing patched.

HTTP never leaves the process: tests monkeypatch
:func:`acc.integrations.cluster_http.request_json`.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from acc import gpu_sizing
from acc.capability_dispatch import ParsedInvocation, dispatch_invocations
from acc.config import RoleDefinitionConfig
from acc.integrations import cluster_http
from acc.operating_modes import gate_categories, should_gate_invocation
from acc.oversight import HumanOversightQueue
from acc.skills import SkillRegistry
from acc.skills.skill_runtime import SkillInvocationError

_REPO = Path(__file__).resolve().parent.parent

LLAMA_8B = {
    "model_type": "llama", "hidden_size": 4096, "num_hidden_layers": 32,
    "num_attention_heads": 32, "num_key_value_heads": 8, "head_dim": 128,
    "intermediate_size": 14336, "vocab_size": 128256, "torch_dtype": "bfloat16",
    "max_position_embeddings": 131072, "tie_word_embeddings": False,
}
LLAMA_70B = {
    "model_type": "llama", "hidden_size": 8192, "num_hidden_layers": 80,
    "num_attention_heads": 64, "num_key_value_heads": 8, "intermediate_size": 28672,
    "vocab_size": 128256, "torch_dtype": "bfloat16", "max_position_embeddings": 131072,
}
# DeepSeek-V3-style MLA + MoE geometry.
DEEPSEEK_MLA = {
    "model_type": "deepseek_v3", "hidden_size": 7168, "num_hidden_layers": 61,
    "num_attention_heads": 128, "num_key_value_heads": 128, "intermediate_size": 18432,
    "moe_intermediate_size": 2048, "n_routed_experts": 256, "n_shared_experts": 1,
    "kv_lora_rank": 512, "qk_rope_head_dim": 64, "vocab_size": 129280,
    "torch_dtype": "bfloat16", "max_position_embeddings": 163840,
    "quantization_config": {"quant_method": "fp8"},
}
# Gemma-style hybrid: one global layer every 6, window 1024.
GEMMA_HYBRID = {
    "model_type": "gemma3_text", "hidden_size": 2560, "num_hidden_layers": 36,
    "num_attention_heads": 8, "num_key_value_heads": 4, "head_dim": 256,
    "intermediate_size": 10240, "vocab_size": 262144, "torch_dtype": "bfloat16",
    "sliding_window": 1024, "sliding_window_pattern": 6,
    "max_position_embeddings": 131072,
}


@pytest.fixture(scope="module")
def registry() -> SkillRegistry:
    reg = SkillRegistry()
    reg.load_from(str(_REPO / "skills"))
    return reg


def _run(coro):
    return asyncio.run(coro)


class _Recorder:
    """Stands in for cluster_http.request_json; records calls, returns canned JSON."""

    def __init__(self, response=None, exc: Exception | None = None) -> None:
        self.calls: list[dict] = []
        self.response = response if response is not None else {}
        self.exc = exc

    async def __call__(self, method, url, **kw):
        self.calls.append({"method": method, "url": url, **kw})
        if self.exc:
            raise self.exc
        return self.response


# ---------------------------------------------------------------------------
# acc.gpu_sizing
# ---------------------------------------------------------------------------


class TestGpuSizing:
    def test_llama8b_geometry(self):
        geom = gpu_sizing.geometry(LLAMA_8B)
        # 2 (K,V) * 8 kv heads * 128 head_dim * 32 layers * 2 bytes
        assert gpu_sizing.kv_bytes_per_token(geom, tp=1, kv_bytes=2) == 131072
        # GQA-aware count lands on the published 8.03B.
        assert gpu_sizing.parameter_count(geom) == pytest.approx(8.03e9, rel=0.01)

    def test_tp_shards_kv_heads(self):
        geom = gpu_sizing.geometry(LLAMA_8B)
        assert gpu_sizing.kv_bytes_per_token(geom, tp=2, kv_bytes=2) == 65536
        # ceil(8 / 16) = 1 head per rank, never zero
        assert gpu_sizing.kv_bytes_per_token(geom, tp=16, kv_bytes=2) == 2 * 1 * 128 * 32 * 2

    def test_mla_latent_is_not_sharded(self):
        geom = gpu_sizing.geometry(DEEPSEEK_MLA)
        expected = 61 * (512 + 64) * 2
        assert gpu_sizing.kv_bytes_per_token(geom, tp=1, kv_bytes=2) == expected
        assert gpu_sizing.kv_bytes_per_token(geom, tp=8, kv_bytes=2) == expected

    def test_fp8_quantization_config_halves_weights(self):
        assert gpu_sizing.weight_dtype_bytes(DEEPSEEK_MLA) == 1
        assert gpu_sizing.weight_dtype_bytes(LLAMA_8B) == 2
        awq = dict(LLAMA_8B, quantization_config={"quant_method": "awq", "bits": 4})
        assert gpu_sizing.weight_dtype_bytes(awq) == 0.5
        ct = dict(LLAMA_8B, quantization_config={
            "quant_method": "compressed-tensors",
            "config_groups": {"group_0": {"weights": {"num_bits": 8}}},
        })
        assert gpu_sizing.weight_dtype_bytes(ct) == 1

    def test_sliding_window_hybrid_grows_past_linear(self):
        geom = gpu_sizing.geometry(GEMMA_HYBRID)
        assert (geom["num_swa_layers"], geom["num_global_layers"]) == (30, 6)
        budget = 8 * gpu_sizing.GIB
        hybrid = gpu_sizing.tokens_from_kv_bytes(geom, budget, tp=1, kv_bytes=2)
        linear = budget // gpu_sizing.kv_bytes_per_token(geom, tp=1, kv_bytes=2)
        assert hybrid > 5 * linear  # only 6 of 36 layers keep growing past the window
        # Below the window every layer grows: identical to linear.
        small = 1024 * 36 * 2 * 4 * 256 * 2 // 2
        assert gpu_sizing.tokens_from_kv_bytes(geom, small, tp=1, kv_bytes=2) == 512

    def test_estimate_8b_fits_one_h100(self):
        r = gpu_sizing.estimate(LLAMA_8B, "h100_sxm", max_model_len=8192)
        assert r["fits"] and r["gpus_per_replica"] == 1
        assert r["per_gpu"]["weights_bytes"] == pytest.approx(8.03e9 * 2, rel=0.01)
        # ~66 GiB of KV at 128 KiB/token on an 80 GiB card
        assert 400_000 < r["per_gpu"]["kv_cache_tokens"] < 500_000
        assert r["serving_config"]["max_num_seqs"] == 52

    def test_estimate_70b_needs_tp4_on_h100(self):
        r = gpu_sizing.estimate(LLAMA_70B, "h100_sxm", max_model_len=32768)
        assert r["fits"] and r["gpus_per_replica"] == 4

    def test_estimate_does_not_fit_says_so(self):
        r = gpu_sizing.estimate(LLAMA_70B, "l4", max_model_len=131072)
        assert r["fits"] is False and "does not fit" in r["note"]

    def test_target_concurrency_gives_replicas(self):
        r = gpu_sizing.estimate(LLAMA_8B, "l4", max_model_len=8192,
                                avg_tokens_per_request=2048, target_concurrency=40)
        per = r["max_concurrent_sequences_at_avg"]
        assert per > 0
        assert r["replicas_needed"] == -(-40 // per)
        assert r["gpus_needed"] == r["replicas_needed"] * r["gpus_per_replica"]

    def test_fp8_kv_cache_doubles_tokens(self):
        bf16 = gpu_sizing.estimate(LLAMA_8B, "h100_sxm", max_model_len=8192)
        fp8 = gpu_sizing.estimate(LLAMA_8B, "h100_sxm", max_model_len=8192, kv_cache_dtype="fp8")
        assert fp8["per_gpu"]["kv_cache_tokens"] == pytest.approx(
            2 * bf16["per_gpu"]["kv_cache_tokens"], rel=0.001)

    def test_multimodal_text_config_is_read(self):
        mm = {"model_type": "llava", "text_config": LLAMA_8B}
        assert gpu_sizing.estimate(mm, "h100_sxm", max_model_len=4096)["fits"]

    def test_fails_closed_on_missing_metadata(self):
        with pytest.raises(ValueError, match="insufficient model metadata"):
            gpu_sizing.estimate({"hidden_size": 4096}, "h100_sxm", max_model_len=4096)
        with pytest.raises(ValueError, match="unknown gpu"):
            gpu_sizing.estimate(LLAMA_8B, "tpu_v5", max_model_len=4096)

    def test_skill_wraps_errors(self, registry):
        out = _run(registry.invoke("gpu_capacity_estimate", {
            "model_config": LLAMA_8B, "gpu": "l40s", "max_model_len": 8192}))
        assert out["fits"] is True and out["gpu"]["gpu_id"] == "l40s"
        with pytest.raises(SkillInvocationError, match="insufficient model metadata"):
            _run(registry.invoke("gpu_capacity_estimate", {
                "model_config": {"model_type": "x"}, "gpu": "l40s", "max_model_len": 8192}))


# ---------------------------------------------------------------------------
# prometheus_query
# ---------------------------------------------------------------------------

_VECTOR = {
    "status": "success",
    "data": {"resultType": "vector", "result": [
        {"metric": {"namespace": "flg1", "pod": f"p{i}"}, "value": [1700000000, "1"]}
        for i in range(3)
    ]},
}


class TestPrometheusQuery:
    @pytest.fixture(autouse=True)
    def _env(self, monkeypatch):
        monkeypatch.setenv("ACC_PROMETHEUS_URL", "https://thanos.example:9091/")
        monkeypatch.setenv("ACC_PROMETHEUS_TOKEN", "tok")
        monkeypatch.delenv("ACC_PROMETHEUS_QUERY_ALLOWLIST", raising=False)

    def test_instant_query(self, registry, monkeypatch):
        rec = _Recorder(_VECTOR)
        monkeypatch.setattr(cluster_http, "request_json", rec)
        q = 'sum by (namespace) (kube_pod_container_resource_requests{resource="nvidia_com_gpu"})'
        out = _run(registry.invoke("prometheus_query", {"query": q}))
        assert out["series_count"] == 3 and out["truncated"] is False
        call = rec.calls[0]
        assert call["method"] == "GET"
        assert call["url"] == "https://thanos.example:9091/api/v1/query"
        assert call["params"] == {"query": q}
        assert call["token"] == "tok"

    def test_series_cap(self, registry, monkeypatch):
        monkeypatch.setattr(cluster_http, "request_json", _Recorder(_VECTOR))
        out = _run(registry.invoke("prometheus_query", {"query": "up", "max_series": 2}))
        assert len(out["series"]) == 2 and out["series_count"] == 3 and out["truncated"]

    def test_range_keeps_latest_points(self, registry, monkeypatch):
        matrix = {"status": "success", "data": {"resultType": "matrix", "result": [
            {"metric": {}, "values": [[t, str(t)] for t in range(10)]}]}}
        rec = _Recorder(matrix)
        monkeypatch.setattr(cluster_http, "request_json", rec)
        out = _run(registry.invoke("prometheus_query", {
            "query": "vllm:kv_cache_usage_perc", "mode": "range",
            "start": "1700000000", "end": "1700003600", "step": "60s", "max_points": 3}))
        assert out["series"][0]["values"] == [[7, "7"], [8, "8"], [9, "9"]]
        assert out["truncated"] is True
        assert rec.calls[0]["url"].endswith("/api/v1/query_range")
        assert rec.calls[0]["params"]["step"] == "60s"

    def test_range_needs_start_and_step(self, registry, monkeypatch):
        monkeypatch.setattr(cluster_http, "request_json", _Recorder(_VECTOR))
        with pytest.raises(SkillInvocationError, match="start and step"):
            _run(registry.invoke("prometheus_query", {"query": "up", "mode": "range"}))

    def test_allowlist_refuses_before_any_request(self, registry, monkeypatch):
        rec = _Recorder(_VECTOR)
        monkeypatch.setattr(cluster_http, "request_json", rec)
        monkeypatch.setenv("ACC_PROMETHEUS_QUERY_ALLOWLIST", r"vllm:.*;kube_pod_container_resource_requests\{.*\}")
        _run(registry.invoke("prometheus_query", {"query": "vllm:num_requests_running"}))
        with pytest.raises(SkillInvocationError, match="not in ACC_PROMETHEUS_QUERY_ALLOWLIST"):
            _run(registry.invoke("prometheus_query", {"query": "count(kube_secret_info)"}))
        assert len(rec.calls) == 1

    def test_fail_closed_without_url(self, registry, monkeypatch):
        monkeypatch.delenv("ACC_PROMETHEUS_URL")
        with pytest.raises(SkillInvocationError, match="ACC_PROMETHEUS_URL is not set"):
            _run(registry.invoke("prometheus_query", {"query": "up"}))

    def test_prometheus_error_surfaces(self, registry, monkeypatch):
        monkeypatch.setattr(cluster_http, "request_json",
                            _Recorder({"status": "error", "error": "parse error"}))
        with pytest.raises(SkillInvocationError, match="parse error"):
            _run(registry.invoke("prometheus_query", {"query": "up{"}))

    def test_falls_back_to_service_account_token(self, registry, monkeypatch, tmp_path):
        (tmp_path / "token").write_text("sa-token\n")
        monkeypatch.setenv("ACC_SA_DIR", str(tmp_path))
        monkeypatch.delenv("ACC_PROMETHEUS_TOKEN")
        rec = _Recorder(_VECTOR)
        monkeypatch.setattr(cluster_http, "request_json", rec)
        _run(registry.invoke("prometheus_query", {"query": "up"}))
        assert rec.calls[0]["token"] == "sa-token"


# ---------------------------------------------------------------------------
# ocp_model_control
# ---------------------------------------------------------------------------

_STOP_ARGS = {"action": "stop", "namespace": "flg1", "name": "diffusiongemma-26b-a4b",
              "reason": "idle for 6h; frees 1 GPU for minimax"}


def _patched(stop: str) -> dict:
    return {"metadata": {"annotations": {"serving.kserve.io/stop": stop}}}


class TestOcpModelControl:
    @pytest.fixture(autouse=True)
    def _env(self, monkeypatch):
        monkeypatch.setenv("ACC_OCP_CONTROL_NAMESPACES", "flg1, flg2")
        monkeypatch.setenv("ACC_KUBERNETES_API_URL", "https://api.dgx.example:6443")
        monkeypatch.setenv("ACC_KUBERNETES_TOKEN", "sa")

    def test_stop_patches_the_annotation(self, registry, monkeypatch):
        rec = _Recorder(_patched("true"))
        monkeypatch.setattr(cluster_http, "request_json", rec)
        out = _run(registry.invoke("ocp_model_control", dict(_STOP_ARGS)))
        assert out["stopped"] is True and out["dry_run"] is False
        call = rec.calls[0]
        assert call["method"] == "PATCH"
        assert call["url"] == ("https://api.dgx.example:6443/apis/serving.kserve.io/v1alpha1"
                               "/namespaces/flg1/llminferenceservices/diffusiongemma-26b-a4b")
        assert call["headers"]["Content-Type"] == "application/merge-patch+json"
        ann = call["json_body"]["metadata"]["annotations"]
        assert ann["serving.kserve.io/stop"] == "true"
        assert ann["acc.ic3networks.com/model-control-reason"] == _STOP_ARGS["reason"]
        assert call["params"] is None

    def test_start_and_inferenceservice(self, registry, monkeypatch):
        rec = _Recorder(_patched("false"))
        monkeypatch.setattr(cluster_http, "request_json", rec)
        out = _run(registry.invoke("ocp_model_control", dict(
            _STOP_ARGS, action="start", kind="InferenceService")))
        assert out["stopped"] is False
        assert "/v1beta1/namespaces/flg1/inferenceservices/" in rec.calls[0]["url"]
        assert rec.calls[0]["json_body"]["metadata"]["annotations"]["serving.kserve.io/stop"] == "false"

    def test_dry_run_is_server_side(self, registry, monkeypatch):
        rec = _Recorder(_patched("true"))
        monkeypatch.setattr(cluster_http, "request_json", rec)
        out = _run(registry.invoke("ocp_model_control", dict(_STOP_ARGS, dry_run=True)))
        assert out["dry_run"] is True
        assert rec.calls[0]["params"] == {"dryRun": "All"}

    def test_namespace_outside_allowlist_is_refused_without_a_request(self, registry, monkeypatch):
        rec = _Recorder(_patched("true"))
        monkeypatch.setattr(cluster_http, "request_json", rec)
        with pytest.raises(SkillInvocationError, match="not in ACC_OCP_CONTROL_NAMESPACES"):
            _run(registry.invoke("ocp_model_control", dict(_STOP_ARGS, namespace="openshift-monitoring")))
        assert rec.calls == []

    def test_unset_allowlist_refuses_everything(self, registry, monkeypatch):
        rec = _Recorder(_patched("true"))
        monkeypatch.setattr(cluster_http, "request_json", rec)
        monkeypatch.setenv("ACC_OCP_CONTROL_NAMESPACES", " , ")
        with pytest.raises(SkillInvocationError, match="fail-closed"):
            _run(registry.invoke("ocp_model_control", dict(_STOP_ARGS)))
        assert rec.calls == []

    def test_api_error_surfaces(self, registry, monkeypatch):
        monkeypatch.setattr(cluster_http, "request_json", _Recorder(
            exc=cluster_http.ClusterHTTPError("PATCH ...: HTTP 403: forbidden", status=403)))
        with pytest.raises(SkillInvocationError, match="403"):
            _run(registry.invoke("ocp_model_control", dict(_STOP_ARGS)))

    # -- the gate --------------------------------------------------------

    def test_gated_in_every_mode_that_dispatches(self, registry):
        m = registry.manifest("ocp_model_control")
        cats = gate_categories("skill", "ocp_model_control", m)
        assert cats  # system_access
        for mode in ("AUTO", "ACCEPT_EDITS", "ASK_PERMISSIONS"):
            assert should_gate_invocation(mode, kind="skill", target="ocp_model_control",
                                          risk_level=m.risk_level, categories=cats), mode

    def test_read_skills_are_not_gated_in_auto(self, registry):
        for skill in ("prometheus_query", "gpu_capacity_estimate"):
            m = registry.manifest(skill)
            cats = gate_categories("skill", skill, m)
            assert not should_gate_invocation("AUTO", kind="skill", target=skill,
                                              risk_level=m.risk_level, categories=cats), skill

    def _core(self, registry):
        class _Core:
            _skill_registry = registry
            _mcp_registry = None

            async def invoke_skill(self, skill_id, args, role):
                return await registry.invoke(skill_id, args)

        return _Core()

    def _role(self):
        return RoleDefinitionConfig(
            allowed_skills=["ocp_model_control"],
            allowed_actions=["control_model_serving"],
            max_skill_risk_level="HIGH",
        )

    def _inv(self):
        import json
        return ParsedInvocation(kind="skill", target="ocp_model_control", args=dict(_STOP_ARGS),
                                raw=f"[SKILL: ocp_model_control {json.dumps(_STOP_ARGS)}]")

    @pytest.mark.asyncio
    async def test_approve_patches(self, registry, monkeypatch):
        rec = _Recorder(_patched("true"))
        monkeypatch.setattr(cluster_http, "request_json", rec)
        queue = HumanOversightQueue(redis_client=None, collective_id="t", timeout_s=5)

        async def approver():
            for _ in range(100):
                items = await queue.pending()
                if items:
                    await queue.approve(items[0].oversight_id, "operator")
                    return
                await asyncio.sleep(0.02)

        task = asyncio.create_task(approver())
        outcomes = await dispatch_invocations(
            [self._inv()], self._core(registry), self._role(),
            oversight_queue=queue, task_id="t1", operating_mode="AUTO")
        await task
        assert outcomes[0].ok, outcomes[0].error
        real = [c for c in rec.calls if c["params"] is None]
        assert len(real) == 1  # exactly one real patch, after approval
        assert real[0]["json_body"]["metadata"]["annotations"]["serving.kserve.io/stop"] == "true"

    @pytest.mark.asyncio
    async def test_reject_patches_nothing(self, registry, monkeypatch):
        rec = _Recorder(_patched("true"))
        monkeypatch.setattr(cluster_http, "request_json", rec)
        queue = HumanOversightQueue(redis_client=None, collective_id="t", timeout_s=5)

        async def rejecter():
            for _ in range(100):
                items = await queue.pending()
                if items:
                    await queue.reject(items[0].oversight_id, "operator", "keep it up")
                    return
                await asyncio.sleep(0.02)

        task = asyncio.create_task(rejecter())
        outcomes = await dispatch_invocations(
            [self._inv()], self._core(registry), self._role(),
            oversight_queue=queue, task_id="t2", operating_mode="AUTO")
        await task
        assert outcomes[0].ok is False
        # A preview may have run (server-side dryRun) — nothing real was patched.
        assert all(c["params"] == {"dryRun": "All"} for c in rec.calls)

    @pytest.mark.asyncio
    async def test_plan_mode_dispatches_nothing(self, registry, monkeypatch):
        rec = _Recorder(_patched("true"))
        monkeypatch.setattr(cluster_http, "request_json", rec)
        queue = HumanOversightQueue(redis_client=None, collective_id="t", timeout_s=5)
        outcomes = await dispatch_invocations(
            [self._inv()], self._core(registry), self._role(),
            oversight_queue=queue, task_id="t3", operating_mode="PLAN")
        assert outcomes[0].result == {"plan_mode": True, "would_have_called": "ocp_model_control"}
        assert rec.calls == [] and await queue.pending() == []


# ---------------------------------------------------------------------------
# requires_oversight: no queue -> refused, queue -> always gated
# ---------------------------------------------------------------------------


class TestRequiresOversight:
    @pytest.fixture(autouse=True)
    def _env(self, monkeypatch):
        monkeypatch.setenv("ACC_OCP_CONTROL_NAMESPACES", "flg1")
        monkeypatch.setenv("ACC_KUBERNETES_API_URL", "https://api.dgx.example:6443")
        monkeypatch.setenv("ACC_KUBERNETES_TOKEN", "sa")

    def test_ocp_model_control_declares_it(self, registry):
        assert registry.manifest("ocp_model_control").requires_oversight is True
        for skill in ("prometheus_query", "gpu_capacity_estimate"):
            assert registry.manifest(skill).requires_oversight is False

    @pytest.mark.asyncio
    @pytest.mark.parametrize("mode", ["AUTO", "ACCEPT_EDITS", "ASK_PERMISSIONS"])
    async def test_no_queue_refuses_before_any_request(self, registry, monkeypatch, mode):
        rec = _Recorder(_patched("true"))
        monkeypatch.setattr(cluster_http, "request_json", rec)
        t = TestOcpModelControl()
        outcomes = await dispatch_invocations(
            [t._inv()], t._core(registry), t._role(),
            oversight_queue=None, task_id="nq", operating_mode=mode)
        assert outcomes[0].ok is False
        assert "no oversight queue" in outcomes[0].error
        assert rec.calls == []

    class _Manifest:
        """A LOW-risk manifest with no gate category: only the flag can gate it."""

        def __init__(self, requires_oversight: bool) -> None:
            self.risk_level = "LOW"
            self.purpose = "stub"
            self.system_access = False
            self.acts_on_behalf = False
            self.destructive = False
            self.requires_oversight = requires_oversight

    class _Core:
        def __init__(self, manifest) -> None:
            class _Reg:
                def manifest(self, skill_id):
                    return manifest

            self._skill_registry = _Reg()
            self._mcp_registry = None
            self.calls: list = []

        async def invoke_skill(self, skill_id, args, role):
            self.calls.append(skill_id)
            return {"ok": True}

    def _stub_inv(self):
        return ParsedInvocation(kind="skill", target="stub_act", args={}, raw="[SKILL: stub_act {}]")

    @pytest.mark.asyncio
    async def test_flag_gates_even_a_low_risk_call_in_auto(self):
        queue = HumanOversightQueue(redis_client=None, collective_id="t", timeout_s=5)
        core = self._Core(self._Manifest(requires_oversight=True))
        role = RoleDefinitionConfig(allowed_skills=["stub_act"])

        async def rejecter():
            for _ in range(100):
                items = await queue.pending()
                if items:
                    await queue.reject(items[0].oversight_id, "operator", "no")
                    return
                await asyncio.sleep(0.02)

        task = asyncio.create_task(rejecter())
        outcomes = await dispatch_invocations([self._stub_inv()], core, role,
                                              oversight_queue=queue, task_id="g1", operating_mode="AUTO")
        await task
        assert outcomes[0].ok is False and core.calls == []

    @pytest.mark.asyncio
    async def test_without_the_flag_the_same_call_runs_ungated(self):
        queue = HumanOversightQueue(redis_client=None, collective_id="t", timeout_s=5)
        core = self._Core(self._Manifest(requires_oversight=False))
        role = RoleDefinitionConfig(allowed_skills=["stub_act"])
        outcomes = await dispatch_invocations([self._stub_inv()], core, role,
                                              oversight_queue=queue, task_id="g2", operating_mode="AUTO")
        assert outcomes[0].ok is True and core.calls == ["stub_act"]
        assert await queue.pending() == []
        # ...and with no queue at all it also just runs: the flag is the only refusal.
        core2 = self._Core(self._Manifest(requires_oversight=False))
        outcomes = await dispatch_invocations([self._stub_inv()], core2, role,
                                              oversight_queue=None, task_id="g3", operating_mode="AUTO")
        assert outcomes[0].ok is True
