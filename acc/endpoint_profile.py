"""What an inference endpoint can actually do, asked rather than assumed.

ACC has never put a question to an endpoint. ``check_endpoints``
(``acc/preflight.py:448``) dials the gateway root and reports the HTTP status,
and that is the whole of it — a grep for ``v1/models``, ``/metrics``,
``/tokenize`` or ``api/show`` across ``acc/`` returned one comment and no code.

Three shipped optimisations each rest on an unasked question:

* **PR-CA1** restructured the prompt so the per-role prefix stays contiguous
  and cacheable — assuming the server prefix-caches, and that ACC hits it.
* **PR-CA2** skips the client-side cache hint on vLLM and Ollama because
  *"they already benefit from the stable prefix"* — assuming they cache at all.
* ``acc/backends/llm_vllm.py`` loads a local SentenceTransformer *because* the
  served model may be chat-only with no ``/v1/embeddings`` — never tested.

Each degrades silently. A prefix cache that never hits and a CPU embedder beside
an idle server-side one both present as *"the model is slow and a bit
forgetful"*, which is model weakness misattributed.

This module answers those questions and nothing else. **It is a layer below the
preflight check, not part of it** — ``EndpointProfile`` is structured data that
``preflight.check_endpoint_capabilities`` renders into ``Result`` rows, and that
the context budgeter consumes directly. Importing ``acc.preflight`` here would
force every consumer to drag in the check registry to ask how big a window is.

Three properties carry the design.

**Unknown is a type, not a convention.** Every property is a
:class:`Capability` whose ``value is None`` means *we could not ask*. There are
no sentinel integers and no plausible defaults: a probe that cannot find its
field says which field, and stops. Guessing here would be worse than silence,
because the caller is a budgeter that will size a prompt against the answer.

**Nothing raises.** A DNS blip must not make a deployment look broken, so every
transport failure is caught and recorded in ``errors``. The check above relies
on this — ``preflight.run()`` classifies a raising check as BROKEN.

**Read-only, always.** No probe configures anything. ``--enable-prefix-caching``,
``--max-model-len`` and ``rope_scaling`` are observed, never set. The one
endpoint knob ACC owns is Ollama's per-request ``num_ctx``, and that belongs to
``openspec/changes/20260826-context-budget``, not here.

Three findings from the real lighthouse capture shaped the parsers; see
``tests/fixtures/endpoint/PROVENANCE.md`` for the responses themselves.

1. **HTTP status lies about capability.** vLLM 0.11.2 answers ``POST
   /v1/embeddings`` on a chat-only model with **200** and an error envelope.
   Detection parses the body; an unrouted path still 404s honestly.
2. **Prefix caching is declarative.** ``vllm:cache_config_info`` carries
   ``enable_prefix_caching`` as a label, so *is it on* needs no behavioural
   probe — which collapses most of the ambiguity a hit-rate alone would leave.
3. **The interesting direction is reduction, not extension.** lighthouse serves
   an 8192 window on a model whose native length is 131072 — 6% of it, to fit
   the KV cache in VRAM. A profile that only looked for rope *extension* would
   have called that unremarkable.

Change: ``openspec/changes/20260826-endpoint-capability-profile`` Phase 1.3-1.4.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("acc.endpoint_profile")

__all__ = [
    "Capability",
    "EndpointProfile",
    "probe_endpoint",
    "unknown",
    "NATIVE_WINDOWS",
]

#: How a value was obtained. Recorded so a reader can tell a measurement from a
#: lookup — ``table`` and ``inferred`` are weaker evidence than ``probed`` and
#: a report that blurred them would overstate what ACC knows.
SOURCE_DECLARED = "declared"
SOURCE_PROBED = "probed"
SOURCE_METRICS = "metrics"
SOURCE_TABLE = "table"
SOURCE_INFERRED = "inferred"

_DEFAULT_TIMEOUT_S = 5.0

#: Native (trained) context lengths, keyed on a case-insensitive substring of
#: the served model id. Only ever used to give ``served_window`` something to be
#: compared against; a miss yields unknown rather than a guess.
#:
#: Deliberately short. A long table is a maintenance burden that ages badly, and
#: the comparison it feeds is advisory — the number ACC budgets against is
#: always the *served* one.
NATIVE_WINDOWS: dict[str, int] = {
    "llama-3.2-1b": 131072,
    "llama-3.2-3b": 131072,
    "llama-3.1-8b": 131072,
    "llama-3.1-70b": 131072,
    "qwen2.5-7b": 32768,
    "qwen2.5-14b": 32768,
    "qwen2.5-32b": 32768,
    "qwen3-14b": 32768,
    "mistral-7b": 32768,
}

#: Anthropic publishes no discovery endpoint for context length, so it is a
#: table or nothing. Keyed on a prefix of the configured model string.
_ANTHROPIC_WINDOWS: dict[str, int] = {
    "claude-opus": 200000,
    "claude-sonnet": 200000,
    "claude-haiku": 200000,
}


@dataclass(frozen=True)
class Capability:
    """One probed property, or an explicit inability to probe it.

    ``value is None`` is the *only* representation of unknown. Call sites test
    ``cap.known`` rather than comparing against a sentinel, so a future field
    whose legitimate value is ``0`` or ``False`` cannot be mistaken for absent.
    """

    value: Any | None = None
    source: str = ""
    note: str = ""

    @property
    def known(self) -> bool:
        return self.value is not None

    def as_dict(self) -> dict[str, Any]:
        return {"value": self.value, "source": self.source, "note": self.note}


def unknown(note: str) -> Capability:
    """An unknown carrying the reason. The reason is not optional.

    A bare unknown is indistinguishable from a probe nobody wrote, which is the
    state this module exists to leave behind.
    """
    return Capability(None, "", note)


@dataclass(frozen=True)
class EndpointProfile:
    """What one configured model's endpoint was found to support."""

    model_id: str
    backend: str
    base_url: str = ""
    served_model: Capability = field(default_factory=lambda: unknown("not probed"))
    served_window: Capability = field(default_factory=lambda: unknown("not probed"))
    native_window: Capability = field(default_factory=lambda: unknown("not probed"))
    window_scaling: Capability = field(default_factory=lambda: unknown("not probed"))
    prefix_cache_enabled: Capability = field(default_factory=lambda: unknown("not probed"))
    prefix_cache_hits: Capability = field(default_factory=lambda: unknown("not probed"))
    tokenize: Capability = field(default_factory=lambda: unknown("not probed"))
    embeddings: Capability = field(default_factory=lambda: unknown("not probed"))
    kv_cache: Capability = field(default_factory=lambda: unknown("not probed"))
    probed_at: float = 0.0
    errors: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "model_id": self.model_id,
            "backend": self.backend,
            "base_url": self.base_url,
            "probed_at": self.probed_at,
            "errors": list(self.errors),
        }
        for name in (
            "served_model", "served_window", "native_window", "window_scaling",
            "prefix_cache_enabled", "prefix_cache_hits", "tokenize",
            "embeddings", "kv_cache",
        ):
            out[name] = getattr(self, name).as_dict()
        return out


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------

@dataclass
class _Response:
    status: int
    body: str

    def json(self) -> Any | None:
        try:
            return json.loads(self.body)
        except (ValueError, TypeError):
            return None


def _request(
    url: str,
    *,
    timeout_s: float,
    payload: dict | None = None,
    api_key: str = "",
) -> tuple[_Response | None, str]:
    """One HTTP round trip. Returns ``(response, error)``; never raises.

    An HTTP error status is a *response*, not a failure — a 404 from
    ``/tokenize`` is the answer to the question. Only transport-level problems
    produce an error string.

    The Authorization header is built here and never logged. ``urllib`` is used
    rather than ``httpx`` so a diagnostic cannot fail on an optional dependency.
    """
    headers = {"Accept": "application/json"}
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    req = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:  # noqa: S310
            return _Response(resp.status, resp.read().decode("utf-8", "replace")), ""
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", "replace")
        except Exception:  # pragma: no cover - body already consumed
            pass
        return _Response(exc.code, body), ""
    except Exception as exc:
        # Redact rather than reason. An earlier version argued that urllib
        # never echoes the Authorization header into an exception, which is
        # true and insufficient: the key can reach an error string by other
        # routes (a URL, a wrapped library's context), and a test planting a
        # sentinel proved it. REQ-CRD-002 is a hard rule, so it is enforced
        # here at the one place every error string is born.
        return None, _redact(f"{type(exc).__name__}: {exc}", api_key)


def _redact(text: str, secret: str) -> str:
    """Remove *secret* from *text*. The last line of defence for REQ-CRD-002.

    Applied to every error string before it can reach ``errors`` or a ``note``,
    regardless of whether the caller believes a credential could be in there --
    a belief is what failed the first time.
    """
    if not secret or not text:
        return text
    return text.replace(secret, "<redacted>")


def _api_key_for(entry: Any, environ: dict[str, str]) -> tuple[str, str]:
    """Resolve the configured key. Returns ``(key, note)``.

    The note names the *variable*, never the value — that distinction is the
    whole of preflight's secret discipline, narrowed to its intent: a check may
    use a credential, it may not report one.
    """
    name = (getattr(entry, "api_key_env", "") or "").strip()
    if not name:
        return "", ""
    key = environ.get(name, "")
    if not key:
        return "", f"api key env {name} is not set"
    return key, ""


def _base_of(entry: Any) -> str:
    return (getattr(entry, "base_url", "") or "").rstrip("/")


def _root_of(base_url: str) -> str:
    """The server root, for endpoints that do not live under ``/v1``.

    vLLM serves ``/metrics`` and ``/tokenize`` at the root while the OpenAI
    surface sits under ``/v1``, and ``models.yaml`` conventionally configures
    the ``/v1`` form.
    """
    return base_url[: -len("/v1")] if base_url.endswith("/v1") else base_url


# ---------------------------------------------------------------------------
# Parsers — each written against a captured fixture, each degrading to unknown
# ---------------------------------------------------------------------------

def _parse_models(doc: Any, want_model: str) -> tuple[Capability, Capability]:
    """``GET /v1/models`` -> (served_model, served_window).

    ``max_model_len`` is a vLLM extension to the OpenAI ModelCard; a gateway
    that does not add it yields unknown for the window while still naming the
    model.
    """
    data = (doc or {}).get("data") if isinstance(doc, dict) else None
    if not isinstance(data, list) or not data:
        return unknown("no data[] in /v1/models"), unknown("no data[] in /v1/models")

    card = None
    if want_model:
        card = next(
            (c for c in data if isinstance(c, dict) and c.get("id") == want_model),
            None,
        )
    if card is None:
        card = next((c for c in data if isinstance(c, dict)), None)
    if card is None:
        return unknown("no usable model card"), unknown("no usable model card")

    served = Capability(card.get("id"), SOURCE_PROBED) if card.get("id") else \
        unknown("model card has no id")

    raw = card.get("max_model_len")
    if isinstance(raw, int) and raw > 0:
        window = Capability(raw, SOURCE_PROBED)
    else:
        window = unknown("model card has no max_model_len (not a vLLM server?)")
    return served, window


def _parse_tokenize(resp: _Response) -> tuple[Capability, Capability]:
    """``POST /tokenize`` -> (tokenize_available, served_window).

    vLLM returns ``max_model_len`` alongside the token count, which gives the
    window a second independent source on this backend.
    """
    if resp.status == 404:
        return Capability(False, SOURCE_PROBED, "no /tokenize endpoint"), \
            unknown("no /tokenize endpoint")

    doc = resp.json()
    if not isinstance(doc, dict) or _error_of(doc) is not None:
        return (
            Capability(False, SOURCE_PROBED, _error_of(doc) or "unparseable response"),
            unknown("tokenize returned an error"),
        )
    if "count" not in doc:
        return unknown("no count in /tokenize response"), unknown("no count")

    raw = doc.get("max_model_len")
    window = (
        Capability(raw, SOURCE_PROBED)
        if isinstance(raw, int) and raw > 0
        else unknown("no max_model_len in /tokenize response")
    )
    return Capability(True, SOURCE_PROBED, "exact token counting available"), window


def _error_of(doc: Any) -> str | None:
    """The message from an OpenAI-style error envelope, if this is one.

    Load-bearing: vLLM 0.11.2 answers an unsupported-but-routed endpoint with
    **HTTP 200** and this envelope. A probe that trusted the status code would
    report the capability as present.
    """
    if not isinstance(doc, dict):
        return None
    err = doc.get("error")
    if isinstance(err, dict):
        return str(err.get("message") or "error")
    if isinstance(err, str):
        return err
    return None


def _parse_embeddings(resp: _Response) -> Capability:
    """``POST /v1/embeddings`` -> whether the server can embed.

    See :func:`_error_of`. Status alone is not evidence here.
    """
    if resp.status == 404:
        return Capability(False, SOURCE_PROBED, "no /v1/embeddings endpoint")

    doc = resp.json()
    message = _error_of(doc)
    if message is not None:
        return Capability(False, SOURCE_PROBED, message)
    if isinstance(doc, dict) and doc.get("data"):
        return Capability(
            True, SOURCE_PROBED,
            "server-side embeddings available; local embedder may be redundant",
        )
    if resp.status >= 400:
        return Capability(False, SOURCE_PROBED, f"HTTP {resp.status}")
    return unknown("embeddings response had neither data nor an error envelope")


_METRIC_LINE = re.compile(r"^(?P<name>[a-zA-Z_:][\w:]*)(?P<labels>\{[^}]*\})?\s+(?P<value>[^\s]+)\s*$")
_LABEL_PAIR = re.compile(r'(\w+)="([^"]*)"')


def _scrape(text: str) -> tuple[dict[str, float], dict[str, dict[str, str]]]:
    """Minimal Prometheus text parse: sample values, plus info-metric labels.

    Deliberately not a Prometheus client dependency — a one-shot diagnostic
    should not add a library, and the two families read here are simple.
    """
    values: dict[str, float] = {}
    labels: dict[str, dict[str, str]] = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = _METRIC_LINE.match(line)
        if not m:
            continue
        name = m.group("name")
        try:
            values[name] = float(m.group("value"))
        except ValueError:
            continue
        if m.group("labels"):
            labels[name] = dict(_LABEL_PAIR.findall(m.group("labels")))
    return values, labels


def _parse_metrics(text: str) -> tuple[Capability, Capability, Capability]:
    """``GET /metrics`` -> (prefix_cache_enabled, prefix_cache_hits, kv_cache).

    Two distinct facts about the cache, kept apart on purpose. *Configured on*
    comes from a label and is certain; *hitting* comes from counters and needs
    traffic before it means anything. Reporting one as the other is how a cold
    server gets diagnosed as a misconfiguration.
    """
    values, labels = _scrape(text)
    if not values:
        u = unknown("no parseable metrics")
        return u, u, u

    cfg = labels.get("vllm:cache_config_info", {})
    if "enable_prefix_caching" in cfg:
        enabled = Capability(
            cfg["enable_prefix_caching"].strip().lower() == "true",
            SOURCE_METRICS,
            "from vllm:cache_config_info",
        )
    else:
        enabled = unknown("no enable_prefix_caching label on vllm:cache_config_info")

    # The plain family, NOT vllm:external_prefix_cache_* — that one counts
    # cross-instance KV-connector sharing and reads 0.0 on a single server,
    # which would report a 0% hit rate on a cache that is in fact hitting.
    queries = values.get("vllm:prefix_cache_queries_total")
    hits = values.get("vllm:prefix_cache_hits_total")
    if queries is None or hits is None:
        hit_rate = unknown("no vllm:prefix_cache_{queries,hits}_total")
    elif queries <= 0:
        hit_rate = Capability(
            {"queries": 0, "hits": 0, "rate": None},
            SOURCE_METRICS,
            "no queries yet - cache is cold, not necessarily disabled",
        )
    else:
        hit_rate = Capability(
            {"queries": int(queries), "hits": int(hits), "rate": hits / queries},
            SOURCE_METRICS,
        )

    kv: dict[str, Any] = {}
    if cfg:
        kv["dtype"] = cfg.get("cache_dtype")
        kv["gpu_memory_utilization"] = cfg.get("gpu_memory_utilization")
        try:
            blocks = int(cfg.get("num_gpu_blocks", ""))
            size = int(cfg.get("block_size", ""))
            # The shared pool, in tokens. Divided by the served window this is
            # how many full-length sequences fit at once - the concurrency
            # ceiling an edge deployment actually runs into, well before any
            # single agent overflows.
            kv["pool_tokens"] = blocks * size
        except (TypeError, ValueError):
            pass
    usage = values.get("vllm:kv_cache_usage_perc")
    if usage is not None:
        kv["usage_perc"] = usage
    kv_cap = Capability(kv, SOURCE_METRICS) if kv else unknown("no KV cache metrics")

    return enabled, hit_rate, kv_cap


def _native_window(model: str) -> Capability:
    key = (model or "").lower()
    for needle, length in NATIVE_WINDOWS.items():
        if needle in key:
            return Capability(length, SOURCE_TABLE, f"table match on {needle!r}")
    return unknown(f"no native-length table entry for {model!r}")


def _window_scaling(served: Capability, native: Capability) -> Capability:
    """How the served window relates to the model's trained length.

    Never named YaRN. On the backends ACC talks to, a served window larger than
    the native length is evidence that *some* rope rescaling is configured, and
    nothing distinguishes YaRN from linear position interpolation from here.
    Saying "YaRN" would be a guess wearing a measurement's clothes.

    The reduction case is the one that actually shows up: lighthouse serves 8192
    of a 131072-token model to fit the KV cache in VRAM.
    """
    if not (served.known and native.known):
        return unknown("needs both served and native window")
    ratio = served.value / native.value
    if ratio > 1.01:
        return Capability(
            {"direction": "extended", "ratio": round(ratio, 3)},
            SOURCE_INFERRED,
            "served window exceeds native length, so rope rescaling is "
            "configured; the mechanism is not exposed by this backend",
        )
    if ratio < 0.99:
        return Capability(
            {"direction": "reduced", "ratio": round(ratio, 3)},
            SOURCE_INFERRED,
            f"serving {ratio:.0%} of the model's native length, typically to "
            "fit the KV cache in available memory",
        )
    return Capability({"direction": "native", "ratio": 1.0}, SOURCE_INFERRED)


# ---------------------------------------------------------------------------
# The probes
# ---------------------------------------------------------------------------

def probe_endpoint(
    entry: Any,
    *,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
    environ: dict[str, str] | None = None,
) -> EndpointProfile:
    """Profile the endpoint behind one ``ModelEntry``. Never raises.

    Args:
        entry: a :class:`acc.models.ModelEntry` (duck-typed, so a test double
            with the same attributes works).
        timeout_s: per-request timeout.
        environ: environment to resolve ``api_key_env`` against; defaults to
            the process environment.

    Returns:
        An :class:`EndpointProfile`. Every property it could not establish is
        an unknown carrying the reason.
    """
    env = dict(os.environ) if environ is None else environ
    backend = (getattr(entry, "backend", "") or "").strip().lower()
    model_id = getattr(entry, "model_id", "") or ""

    try:
        if backend == "anthropic":
            return _profile_anthropic(entry, model_id)
        if backend == "ollama":
            return _profile_ollama(entry, model_id, timeout_s)
        if backend in ("vllm", "openai_compat"):
            return _profile_openai_like(entry, model_id, backend, timeout_s, env)
        return EndpointProfile(
            model_id=model_id,
            backend=backend or "unknown",
            base_url=_base_of(entry),
            probed_at=time.time(),
            errors=(f"no probe implemented for backend {backend!r}",),
        )
    except Exception as exc:  # pragma: no cover - belt and braces
        # A diagnostic that crashes while diagnosing is worse than one that
        # reports nothing, and preflight.run() would score a raise as BROKEN.
        logger.debug("endpoint probe raised for %s", model_id, exc_info=True)
        return EndpointProfile(
            model_id=model_id,
            backend=backend or "unknown",
            base_url=_base_of(entry),
            probed_at=time.time(),
            errors=(f"probe raised: {type(exc).__name__}: {exc}",),
        )


def _profile_anthropic(entry: Any, model_id: str) -> EndpointProfile:
    """Table only. Anthropic publishes no context-length discovery endpoint.

    ``/v1/messages/count_tokens`` does give exact input counting, which is what
    ``tokenize`` records — it is a real capability even though nothing was
    dialled to find it.
    """
    model = (getattr(entry, "model", "") or "").strip().lower()
    window = unknown(f"no table entry for anthropic model {model!r}")
    for prefix, length in _ANTHROPIC_WINDOWS.items():
        if model.startswith(prefix):
            window = Capability(length, SOURCE_TABLE, f"table match on {prefix!r}")
            break
    return EndpointProfile(
        model_id=model_id,
        backend="anthropic",
        served_model=Capability(model, SOURCE_DECLARED) if model else unknown("no model set"),
        served_window=window,
        native_window=window,
        window_scaling=Capability({"direction": "native", "ratio": 1.0}, SOURCE_TABLE),
        prefix_cache_enabled=Capability(
            True, SOURCE_TABLE,
            "opt-in per request via cache_control; see llm.enable_prompt_cache",
        ),
        prefix_cache_hits=unknown("reported per response in usage, not by endpoint"),
        tokenize=Capability(True, SOURCE_TABLE, "/v1/messages/count_tokens"),
        embeddings=Capability(False, SOURCE_TABLE, "no embeddings API"),
        kv_cache=unknown("not applicable to a hosted API"),
        probed_at=time.time(),
    )


def _profile_ollama(entry: Any, model_id: str, timeout_s: float) -> EndpointProfile:
    """``POST /api/show`` — the one backend that exposes rope config directly.

    Confidence is lower here than for vLLM: no ACC host currently runs Ollama,
    so this parser ships against an authored fixture rather than a captured one
    (``tests/fixtures/endpoint/PROVENANCE.md``). Every field degrades to
    unknown, so being wrong costs a report line, not a bad budget.
    """
    base = _base_of(entry) or "http://localhost:11434"
    model = (getattr(entry, "model", "") or "").strip()
    errors: list[str] = []

    resp, err = _redacting_caller("", timeout_s)(f"{_root_of(base)}/api/show", {"model": model})
    if resp is None:
        return EndpointProfile(
            model_id=model_id, backend="ollama", base_url=base,
            probed_at=time.time(), errors=(err,),
        )

    doc = resp.json() or {}
    info = doc.get("model_info") if isinstance(doc, dict) else None
    info = info if isinstance(info, dict) else {}

    native = unknown("no <arch>.context_length in model_info")
    for key, val in info.items():
        if key.endswith(".context_length") and isinstance(val, int) and val > 0:
            native = Capability(val, SOURCE_PROBED, f"from {key}")
            break

    scaling_type = next(
        (v for k, v in info.items() if k.endswith(".rope.scaling.type")), None
    )
    if scaling_type:
        detail = {"type": str(scaling_type)}
        for suffix, name in (
            (".rope.scaling.factor", "factor"),
            (".rope.scaling.original_context_length", "original_context_length"),
        ):
            hit = next((v for k, v in info.items() if k.endswith(suffix)), None)
            if hit is not None:
                detail[name] = hit
        # The only backend where the mechanism is read rather than inferred.
        scaling = Capability(detail, SOURCE_PROBED, "from GGUF rope.scaling metadata")
    else:
        scaling = unknown("no rope.scaling.* keys in model_info")

    # num_ctx is what Ollama actually serves, and it defaults far below the
    # model's capacity. ACC does not send it today -- that is the silent
    # truncation 20260826-context-budget fixes -- so what is reported here is
    # the server's own default until that lands.
    params = doc.get("parameters") if isinstance(doc, dict) else None
    num_ctx = None
    if isinstance(params, str):
        m = re.search(r"^\s*num_ctx\s+(\d+)\s*$", params, re.MULTILINE)
        if m:
            num_ctx = int(m.group(1))
    elif isinstance(params, dict) and isinstance(params.get("num_ctx"), int):
        num_ctx = params["num_ctx"]

    if num_ctx:
        served = Capability(
            num_ctx, SOURCE_PROBED, "server num_ctx; ACC does not set it yet",
        )
    else:
        served = unknown(
            "no num_ctx declared - Ollama serves its own default (~4096) "
            "regardless of the model's capacity"
        )

    return EndpointProfile(
        model_id=model_id,
        backend="ollama",
        base_url=base,
        served_model=Capability(model, SOURCE_DECLARED) if model else unknown("no model set"),
        served_window=served,
        native_window=native,
        window_scaling=scaling,
        prefix_cache_enabled=unknown("not exposed by the Ollama API"),
        prefix_cache_hits=unknown("not exposed by the Ollama API"),
        tokenize=Capability(False, SOURCE_TABLE, "no tokenize endpoint"),
        embeddings=Capability(True, SOURCE_TABLE, "/api/embeddings"),
        kv_cache=unknown("not exposed by the Ollama API"),
        probed_at=time.time(),
        errors=tuple(errors),
    )


def _redacting_caller(api_key: str, timeout_s: float):
    """A ``_request`` wrapper that scrubs the credential from every error.

    Redaction lives HERE, at the boundary where a string can enter the profile,
    rather than only inside ``_request`` where it was first written. The
    difference is not academic: a test that stubs the transport bypassed the
    original placement entirely and caught a real leak. REQ-CRD-002 is a
    property of what ACC *reports*, so it is enforced where reporting begins,
    and holds no matter where the error came from.
    """

    def call(url: str, payload: dict | None = None):
        resp, err = _request(
            url, timeout_s=timeout_s, payload=payload, api_key=api_key,
        )
        return resp, _redact(err, api_key)

    return call


def _all_unknown(
    model_id: str,
    backend: str,
    base_url: str,
    note: str,
    *,
    errors: tuple[str, ...] = (),
) -> EndpointProfile:
    """A profile in which nothing could be established, for one stated reason.

    Used for endpoint-level conditions -- chiefly authentication -- where
    reporting some fields as known would be misleading about how much ACC
    actually learned.
    """
    return EndpointProfile(
        model_id=model_id,
        backend=backend,
        base_url=base_url,
        served_model=unknown(note),
        served_window=unknown(note),
        native_window=unknown(note),
        window_scaling=unknown(note),
        prefix_cache_enabled=unknown(note),
        prefix_cache_hits=unknown(note),
        tokenize=unknown(note),
        embeddings=unknown(note),
        kv_cache=unknown(note),
        probed_at=time.time(),
        errors=errors,
    )


def _profile_openai_like(
    entry: Any,
    model_id: str,
    backend: str,
    timeout_s: float,
    env: dict[str, str],
) -> EndpointProfile:
    """vLLM and OpenAI-compatible gateways.

    Four probes, each independent: a failure of one must not cost the others,
    because a gateway that hides ``/metrics`` still has a window worth knowing.
    """
    base = _base_of(entry)
    root = _root_of(base)
    want = (getattr(entry, "model", "") or "").strip()
    api_key, key_note = _api_key_for(entry, env)
    call = _redacting_caller(api_key, timeout_s)
    errors: list[str] = []

    # Authentication is a property of the ENDPOINT, not of one probe. If the
    # credential is absent or rejected, every capability is unknowable for the
    # same reason -- and firing three more probes would be pointless, would
    # constitute the unauthenticated retry REQ-CRD-003 forbids, and (as a test
    # caught) would let the /tokenize window fallback mask the auth problem
    # with a plausible-looking answer.
    if key_note:
        return _all_unknown(model_id, backend, base, key_note)

    served_model = served_window = unknown("not probed")
    resp, err = call(f"{base}/models")
    if resp is None:
        # Unreachable is an ENDPOINT condition too. The remaining three probes
        # target the same host and port, so they will fail the same way -- and
        # a doctor run that spends 4x the timeout per dead endpoint instead of
        # 1x is a diagnostic nobody waits for.
        return _all_unknown(
            model_id, backend, base, f"unreachable: {err}", errors=(f"GET /v1/models: {err}",),
        )
    if resp.status == 401:
        return _all_unknown(
            model_id, backend, base,
            "endpoint rejected the configured credential (401)",
        )
    else:
        served_model, served_window = _parse_models(resp.json(), want)

    tokenize = unknown("not probed")
    resp, err = call(f"{root}/tokenize", {"model": want, "prompt": "hello world from acc"})
    if resp is None:
        errors.append(f"POST /tokenize: {err}")
        tokenize = unknown(f"transport failure: {err}")
    else:
        tokenize, window_from_tokenize = _parse_tokenize(resp)
        if not served_window.known and window_from_tokenize.known:
            served_window = window_from_tokenize

    embeddings = unknown("not probed")
    resp, err = call(f"{base}/embeddings", {"model": want, "input": "hi"})
    if resp is None:
        errors.append(f"POST /v1/embeddings: {err}")
        embeddings = unknown(f"transport failure: {err}")
    else:
        embeddings = _parse_embeddings(resp)

    cache_on = cache_hits = kv = unknown("not probed")
    resp, err = call(f"{root}/metrics")
    if resp is None:
        errors.append(f"GET /metrics: {err}")
        cache_on = cache_hits = kv = unknown(f"transport failure: {err}")
    elif resp.status >= 400:
        note = f"/metrics returned HTTP {resp.status}"
        cache_on = cache_hits = kv = unknown(note)
    else:
        cache_on, cache_hits, kv = _parse_metrics(resp.body)

    native = _native_window(served_model.value if served_model.known else want)

    return EndpointProfile(
        model_id=model_id,
        backend=backend,
        base_url=base,
        served_model=served_model,
        served_window=served_window,
        native_window=native,
        window_scaling=_window_scaling(served_window, native),
        prefix_cache_enabled=cache_on,
        prefix_cache_hits=cache_hits,
        tokenize=tokenize,
        embeddings=embeddings,
        kv_cache=kv,
        probed_at=time.time(),
        errors=tuple(errors),
    )
