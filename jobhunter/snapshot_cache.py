"""Response cache for the public, read-only instance.

A public instance serves a frozen snapshot: no scheduler, no writes, nothing changes
between one request and the next. Yet the heavy reads (/api/companies/grouped,
/api/network, the graph maps) recompute over a 180 MB database every time, which is
0.5–2 s each on a small hosted machine. Since the answer cannot change, it is computed
once and kept in memory, and it is marked cacheable so the CDN in front of the dashboard
(Vercel's edge, through the /zapi rewrite) answers repeat visits — even while the
backend is asleep, through stale-while-revalidate.

Off entirely unless ZONULY_PUBLIC is set: on your own machine the data moves under the
scheduler and must never be served stale. Operator requests (x-api-key) carry
unredacted rows and are never cached, here or downstream.
"""

from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict

from starlette.datastructures import Headers

from jobhunter import access

log = logging.getLogger(__name__)

#: live probes and per-request state — never answered from memory
_SKIP = ("/api/health", "/api/tasks", "/api/research")

#: browser keeps it briefly; the CDN keeps it longer and serves stale while it refetches
CACHE_CONTROL = b"public, max-age=60, s-maxage=600, stale-while-revalidate=604800"

#: what the dashboard opens first, fetched at start so the first visitor does not pay
PREWARM = (
    "/api/companies/grouped",
    "/api/companies/grouped?include_rejects=true",
    "/api/companies/filling",
    "/api/network",
    "/api/overview",
    "/api/events",
    "/api/audit",
    "/api/graph/map?view=market",
    "/api/graph/map?view=pipeline",
    "/api/graph/map?view=project",
    "/api/jobs?sort=recent&limit=500",
    "/api/lists/india-star",
    "/api/leads",
    "/api/leads?source=all&city=all&who=ready&offset=0&limit=50",
    "/api/companies",
)


class SnapshotCache:
    """Pure ASGI middleware: GET 200 JSON responses, kept by path + query, LRU-bounded."""

    def __init__(self, app, max_bytes: int = 96_000_000):
        self.app = app
        self.max_bytes = max_bytes
        self.store: OrderedDict[str, tuple[int, list, bytes]] = OrderedDict()
        self.size = 0

    def _cacheable(self, scope) -> bool:
        if not access.PUBLIC or scope["type"] != "http" or scope["method"] != "GET":
            return False
        path = scope["path"]
        if not path.startswith("/api/") or path.startswith(_SKIP):
            return False
        return not access.is_operator(Headers(scope=scope))

    async def __call__(self, scope, receive, send):
        if not self._cacheable(scope):
            await self.app(scope, receive, send)
            return

        key = scope["path"] + "?" + scope.get("query_string", b"").decode("latin-1")
        hit = self.store.get(key)
        if hit is not None:
            self.store.move_to_end(key)
            status, headers, body = hit
            await send({"type": "http.response.start", "status": status,
                        "headers": [*headers, (b"x-snapshot-cache", b"hit")]})
            await send({"type": "http.response.body", "body": body})
            return

        start: dict = {}
        chunks: list[bytes] = []

        async def capture(message):
            if message["type"] == "http.response.start":
                start["status"] = message["status"]
                if message["status"] == 200:
                    message = {**message, "headers": [
                        *[(k, v) for k, v in message.get("headers", []) if k.lower() != b"cache-control"],
                        (b"cache-control", CACHE_CONTROL),
                    ]}
                # A copy: the gzip layer outside edits this list in place once it is sent
                # (content-encoding added, content-length dropped), which would label a
                # plain cached body as gzip.
                start["headers"] = list(message.get("headers", []))
            elif message["type"] == "http.response.body":
                chunks.append(message.get("body", b""))
                if not message.get("more_body") and start.get("status") == 200:
                    self._keep(key, start["headers"], b"".join(chunks))
            await send(message)

        await self.app(scope, receive, capture)

    def _keep(self, key: str, headers: list, body: bytes) -> None:
        ctype = next((v for k, v in headers if k.lower() == b"content-type"), b"")
        if not ctype.startswith(b"application/json") or len(body) > self.max_bytes // 4:
            return
        if key in self.store:
            self.size -= len(self.store.pop(key)[2])
        self.store[key] = (200, headers, body)
        self.size += len(body)
        while self.size > self.max_bytes and self.store:
            _, (_, _, old) = self.store.popitem(last=False)
            self.size -= len(old)


async def prewarm(app) -> None:
    """Fetch PREWARM through the full middleware stack, in-process, one at a time."""
    if not access.PUBLIC:
        return
    import httpx

    await asyncio.sleep(0.5)  # let startup finish and the first real request get in first
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://prewarm") as client:
        for path in PREWARM:
            try:
                await client.get(path, timeout=120)
            except Exception as e:  # a failed warm-up only means the first visitor waits
                log.warning("prewarm %s failed: %s", path, e)
    log.info("prewarmed %d public reads", len(PREWARM))
