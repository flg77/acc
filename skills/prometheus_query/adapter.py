"""prometheus_query — read-only PromQL (instant / range) against Thanos or Prometheus."""

from __future__ import annotations

import os
import re
from typing import Any

from acc.integrations import cluster_http
from acc.skills import Skill
from acc.skills.skill_runtime import SkillInvocationError

URL_ENV = "ACC_PROMETHEUS_URL"
TOKEN_ENV = "ACC_PROMETHEUS_TOKEN"
CA_ENV = "ACC_PROMETHEUS_CA_FILE"
ALLOWLIST_ENV = "ACC_PROMETHEUS_QUERY_ALLOWLIST"


def allowlist() -> list[re.Pattern[str]]:
    raw = os.environ.get(ALLOWLIST_ENV, "")
    out = []
    for part in re.split(r"[;\n]", raw):
        part = part.strip()
        if part:
            out.append(re.compile(part))
    return out


def _check_allowed(query: str) -> None:
    patterns = allowlist()
    if patterns and not any(p.fullmatch(query.strip()) for p in patterns):
        raise SkillInvocationError(
            f"prometheus_query: query is not in {ALLOWLIST_ENV} (refused): {query!r}"
        )


class PrometheusQuerySkill(Skill):
    async def invoke(self, args: dict[str, Any]) -> dict[str, Any]:
        base = os.environ.get(URL_ENV, "").strip().rstrip("/")
        if not base:
            raise SkillInvocationError(f"prometheus_query: {URL_ENV} is not set (fail-closed).")
        query = args["query"]
        _check_allowed(query)
        mode = args.get("mode", "instant")
        params: dict[str, Any] = {"query": query}
        if mode == "range":
            if not args.get("start") or not args.get("step"):
                raise SkillInvocationError("prometheus_query: mode=range needs start and step")
            params["start"] = args["start"]
            params["step"] = args["step"]
            if args.get("end"):
                params["end"] = args["end"]
            else:
                import time  # noqa: PLC0415
                params["end"] = f"{time.time():.3f}"
            path = "/api/v1/query_range"
        else:
            if args.get("time"):
                params["time"] = args["time"]
            path = "/api/v1/query"
        try:
            data = await cluster_http.request_json(
                "GET", base + path,
                token=cluster_http.bearer_token(TOKEN_ENV),
                params=params,
                verify=cluster_http.ca_bundle(CA_ENV),
                timeout_s=float(args.get("timeout_s", 30)),
            )
        except cluster_http.ClusterHTTPError as exc:
            raise SkillInvocationError(f"prometheus_query: {exc}") from exc
        if not isinstance(data, dict) or data.get("status") != "success":
            err = data.get("error") if isinstance(data, dict) else data
            raise SkillInvocationError(f"prometheus_query: query failed: {err}")
        return shape(data.get("data") or {}, data.get("warnings") or [],
                     max_series=int(args.get("max_series", 50)),
                     max_points=int(args.get("max_points", 200)))


def shape(data: dict, warnings: list, *, max_series: int, max_points: int) -> dict[str, Any]:
    """Prometheus ``data`` -> compact series, capped. Scalars and strings
    become one series with an empty label set."""
    result_type = str(data.get("resultType", ""))
    raw = data.get("result")
    if result_type in ("scalar", "string"):
        raw = [{"metric": {}, "value": raw}]
    raw = raw if isinstance(raw, list) else []
    truncated = len(raw) > max_series
    series = []
    for item in raw[:max_series]:
        entry: dict[str, Any] = {"metric": item.get("metric") or {}}
        if "values" in item:
            values = item["values"] or []
            if len(values) > max_points:
                truncated = True
                values = values[-max_points:]
            entry["values"] = values
        else:
            entry["value"] = item.get("value")
        series.append(entry)
    return {
        "result_type": result_type,
        "series": series,
        "series_count": len(raw),
        "truncated": truncated,
        "warnings": [str(w) for w in warnings],
    }
