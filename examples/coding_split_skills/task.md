# Task — Add an HTTP rate limiter

A natural-language version of the same task in `plan.yaml`. Use this
when running an ad-hoc operator submission from the prompt pane
(target role: `coding_agent_architect` then follow-on dispatches via
`/cluster show`, etc.) instead of the pre-built plan.

---

## Goal

Add a token-bucket HTTP rate limiter to the FastAPI gateway that:

- Throttles requests per source IP, per API key, and per global
  ceiling (whichever is the lower of the three).
- Supports a pluggable backend — start with an in-memory map; allow
  a redis-backed backend swap-in for multi-instance deployments.
- Returns `429 Too Many Requests` with a `Retry-After` header when
  any bucket is empty.
- Exposes prometheus metrics for per-bucket utilisation.
- Ships with pytest coverage for every public symbol.

## Quality bar

- All public symbols typed (Python 3.12+ syntax — `dict[str, int]`,
  not `Dict[str, int]`).
- No global mutable state outside the backend.
- Tests do not require redis at run time (memory backend is the
  default test fixture).
- New dependencies do not introduce CVE-affected versions or
  license incompatibilities.

## Out of scope

- Rate-limit configuration via a control plane. The first version
  reads its ceilings from environment variables / pyproject config.
- Auth integration beyond reading `request.state.api_key` set by
  upstream middleware.
- Multi-region distributed rate-limit consensus (single-region
  redis is sufficient).
