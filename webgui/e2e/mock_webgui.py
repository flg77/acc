"""The real acc-webgui FastAPI app with a fake bus and (cluster mode) a fake Kubernetes API.

    MODE=cluster|edge  PORT=8080  python e2e/mock_webgui.py

Run from the repo root (acc + tests importable). Synthetic data only. This is
the e2e fixture behind webgui/e2e/*.spec.ts — the same script used by hand
during Phase 2/3 development, checked in so the Playwright suite is
reproducible without the operator's own bb3/lighthouse access.
"""
import copy
import hashlib
import os
import sys
import tempfile
import threading
import time as _t
from http.server import ThreadingHTTPServer
from pathlib import Path

import yaml

MODE = os.environ.get("MODE", "cluster")
PORT = int(os.environ.get("PORT", "8080"))
sys.path.insert(0, os.getcwd())

work = Path(tempfile.mkdtemp(prefix="acc-e2e-"))
os.environ.update({
    "ACC_COLLECTIVE_IDS": "mortgage-agents" if MODE == "cluster" else "sol-01",
    "ACC_WEBGUI_AUTH_MODE": "none",
    "ACC_AUDIT_FILE_PATH": str(work / "no-audit"),
    "ACC_LANCEDB_PATH": str(work / "no-lancedb"),
})

import acc.tui.client as tui_client  # noqa: E402


class FakeObserver:
    def __init__(self, nats_url, collective_id, update_queue, nkey_seed_path=None):
        self.collective_id = collective_id

    async def connect(self): return None
    async def subscribe(self): return None
    async def close(self): return None
    async def publish(self, subject, payload): return None


tui_client.NATSObserver = FakeObserver

if MODE == "cluster":
    from tests import test_deployment_agentset as t

    objects = copy.deepcopy(t.OBJECTS)
    objects["agentcorpora"][0]["spec"]["observability"] = {
        "backend": "otel",
        "otelCollector": {
            "endpoint": "mortgage-agents-corpus-otel-collector:4317",
            "mlflowEndpoint": "https://mlflow.example.svc:8443",
            "mlflowWorkspace": t.NS, "mlflowExperimentID": "2",
        },
    }
    objects["accpackageinstalls"][0]["status"]["phase"] = "Installed"
    t._API.objects = objects
    t._API.refuse = {}
    server = ThreadingHTTPServer(("127.0.0.1", 0), t._API)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    sa = work / "sa"
    sa.mkdir()
    (sa / "token").write_text("sa-token\n")
    (sa / "namespace").write_text(t.NS + "\n")
    os.environ.update({
        "ACC_SERVICEACCOUNT_DIR": str(sa),
        "ACC_KUBERNETES_API": f"http://127.0.0.1:{server.server_address[1]}",
        "ACC_DEPLOY_MODE": "rhoai",
        "ACC_CORPUS_NAME": "mortgage-agents-corpus",
    })
    AGENTS = {
        "a1": {"role": "mortgage_underwriter", "llm_model": "gpt-oss-120b", "state": "ACTIVE",
               "queue_depth": 2, "backpressure_state": "OPEN", "drift_score": 0.04,
               "compliance_score": 0.98,
               "task_progress": {"current_step": 2, "total_steps_estimated": 5, "step_label": "reading the queue"}},
        "a2": {"role": "mortgage_borrower", "llm_model": "stand-in", "state": "ACTIVE",
               "queue_depth": 0, "backpressure_state": "OPEN", "drift_score": 0.01, "compliance_score": 1.0},
        "a3": {"role": "arbiter", "llm_model": "", "state": "ACTIVE",
               "queue_depth": 1, "backpressure_state": "THROTTLE", "drift_score": 0.02, "compliance_score": 0.95},
    }
    CID = "mortgage-agents"
else:
    coll = work / "collective.yaml"
    coll.write_text(
        "collective_id: sol-01\nagents:\n  - role: assistant\n  - role: analyst\n"
        "    replicas: 2\n    model: qwen3-14b\n", encoding="utf-8")
    os.environ["ACC_COLLECTIVE_PATH"] = str(coll)
    AGENTS = {
        "a1": {"role": "assistant", "llm_model": "qwen3-14b", "state": "ACTIVE",
               "queue_depth": 1, "backpressure_state": "OPEN", "drift_score": 0.03, "compliance_score": 0.99},
        "a2": {"role": "analyst", "llm_model": "openai/qwen3-14b-maas", "state": "ACTIVE",
               "queue_depth": 0, "backpressure_state": "OPEN", "drift_score": 0.02, "compliance_score": 1.0},
        "a3": {"role": "arbiter", "llm_model": "", "state": "ACTIVE",
               "queue_depth": 0, "backpressure_state": "OPEN", "drift_score": 0.0, "compliance_score": 1.0},
    }
    CID = "sol-01"

# ── data for Overview / Work / Packages ─────────────────────────────────────
from acc.work_board import WorkItem  # noqa: E402
import acc.work_board as wb  # noqa: E402
import acc.pkg.catalog as cat  # noqa: E402

_now = _t.time()
BOARD = [
    WorkItem(id="s1", kind="plan_step", title="Draft the credit memo", status="RUNNING", role="mortgage_underwriter",
             agent_id="a1", status_detail="calling uw_queue_view", plan_id="p1", step_id="s1", updated_ts=_now - 240),
    WorkItem(id="s2", kind="plan_step", title="Verify income documents", status="QUEUED", role="mortgage_loan_officer",
             plan_id="p1", step_id="s2", updated_ts=_now - 600),
    WorkItem(id="s3", kind="plan_step", title="Issue a condition on application #691", status="BLOCKED",
             role="mortgage_underwriter", plan_id="p1", step_id="s3", blocked_on="ov-7f3a9c21d0e1",
             status_detail="HIGH-risk tool call awaits approval", updated_ts=_now - 90),
    WorkItem(id="s4", kind="plan_step", title="Summarise the queue", status="FAILED", role="mortgage_executive",
             plan_id="p1", step_id="s4", status_detail="the model timed out", iteration="2/3", updated_ts=_now - 1800),
    WorkItem(id="s5", kind="plan_step", title="Answer the prospect's question", status="DONE", role="mortgage_prospect",
             plan_id="p1", step_id="s5", outcome="AUTO_APPROVED", updated_ts=_now - 3000),
]
wb.project_board = lambda **kw: list(BOARD)

_root = work / "catalog"


def _stage(scope, name, ver):
    d = _root / scope
    d.mkdir(parents=True, exist_ok=True)
    pkg = d / f"{name}-{ver}.accpkg"
    pkg.write_bytes(b"FAKE")
    pkg.with_suffix(".accpkg.sha256").write_text(
        hashlib.sha256(pkg.read_bytes()).hexdigest(), encoding="utf-8")


_stage("acc", "mortgage-roles", "1.2.1")
_stage("acc", "workspace-roles", "1.1.0")
_stage("acc", "research-roles", "0.9.4")
_signer = {"issuer": "https://token.actions.githubusercontent.com", "subject_pattern": ".*"}
_sys = work / "catalogs.yaml"
_sys.write_text(yaml.safe_dump({"catalogs": [
    {"id": "workshop-local", "tier": "trusted", "mode": "file", "path": str(_root),
     "required_signer": _signer, "priority": 200},
    {"id": "acc-canonical", "tier": "community", "mode": "https", "url": "https://catalog.example.invalid",
     "required_signer": _signer, "priority": 100},
]}), encoding="utf-8")
os.environ["ACC_SYSTEM_CATALOG"] = str(_sys)
os.environ["ACC_USER_CATALOG"] = str(work / "user-catalogs.yaml")


def _strict(catalog):
    raise cat.IndexFetchError(f"GET {catalog.url}/index.json: no route to host")


cat._fetch_index_https_strict = _strict

_EXTRA = {
    "compliance_health_score": 1.0, "icl_episode_count": 14, "pattern_count": 3,
    "knowledge_feed": [
        {"tag": "credit-policy", "source_agent": "mortgage_underwriter", "confidence": 0.92,
         "snippet": "DTI above 43% needs a compensating factor."},
        {"tag": "queue", "source_agent": "mortgage_loan_officer", "confidence": 0.81,
         "snippet": "Three applications wait for a decision."},
    ],
    "signal_flow_log": [
        {"signal_type": "TASK_ASSIGN", "source_agent": "arbiter", "key_field": "mortgage_underwriter"},
        {"signal_type": "TASK_COMPLETE", "source_agent": "a1", "key_field": "p1/s5"},
    ],
    "episode_nominees": [{"episode_id": "ep-31", "agent": "a1", "score": 0.77, "status": "PENDING"}],
}

from acc.webgui.app import create_app  # noqa: E402

app = create_app()

from acc.webgui.observers import ObserverHub  # noqa: E402

_real_latest = ObserverHub.latest
ObserverHub.latest = (
    lambda self, cid: {"agents": AGENTS, "collective_id": CID, **_EXTRA}
    if cid == CID else _real_latest(self, cid)
)

if __name__ == "__main__":
    import uvicorn
    os.chdir(work)   # workspace-layer writes (catalogs) must land in the temp dir, not the repo
    print(f"mock acc-webgui MODE={MODE} on :{PORT}", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
