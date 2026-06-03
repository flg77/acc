from __future__ import annotations
from typing import Any
from acc.skills import Skill

class HttpGetSkill(Skill):
    async def invoke(self, args: dict[str, Any]) -> dict[str, Any]:
        import httpx  # noqa: PLC0415 — optional dep
        url = args["url"]
        if not (url.startswith("http://") or url.startswith("https://")):
            raise ValueError(f"http_get: url must be http(s): {url!r}")
        timeout = float(args.get("timeout_s", 15))
        cap = int(args.get("max_bytes", 65536))
        headers = args.get("headers") or {}
        if not isinstance(headers, dict):
            raise ValueError("http_get: headers must be a dict")
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as c:
                r = await c.get(url, headers={str(k): str(v) for k, v in headers.items()})
        except Exception as exc:
            raise ValueError(f"http_get: {type(exc).__name__}: {exc}")
        body = r.content[:cap]
        return {"url": str(r.url), "status": r.status_code,
                "content_type": r.headers.get("content-type", ""),
                "body": body.decode("utf-8", errors="replace"),
                "bytes": len(r.content),
                "truncated": len(r.content) > cap}
