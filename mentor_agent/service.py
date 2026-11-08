"""Loopback-only HTTP serving; no authentication or public deployment is implied."""
from __future__ import annotations

import asyncio
import threading

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from starlette.datastructures import Headers
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import JSONResponse

from .runtime import Mentor

BODY_TIMEOUT_SECONDS = 10


class RequestBoundary:
    """Bound and admit requests before JSON parsing or worker-thread dispatch."""

    def __init__(self, app, *, max_body_bytes: int):
        self.app, self.max_body_bytes = app, max_body_bytes
        self.lock = threading.Lock()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "POST" or scope["path"].rstrip("/") != "/ask":
            return await self.app(scope, receive, send)
        headers = Headers(scope=scope)

        async def reject(status, detail):
            await JSONResponse({"detail": detail}, status_code=status)(scope, receive, send)

        origin = headers.get("origin")
        if origin is not None and origin != f"{scope['scheme']}://{headers.get('host', '')}":
            return await reject(403, "Cross-origin requests are not supported.")
        if headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
            return await reject(415, "Use Content-Type: application/json.")
        lengths = headers.getlist("content-length")
        if lengths:
            try:
                if len(lengths) != 1 or int(lengths[0]) < 0:
                    raise ValueError
                if int(lengths[0]) > self.max_body_bytes:
                    return await reject(413, "Request body exceeds the configured limit.")
            except ValueError:
                return await reject(400, "Invalid Content-Length.")
        if not self.lock.acquire(blocking=False):
            return await reject(429, "A request is already running; retry after it completes.")
        try:
            async def read_body():
                body = bytearray()
                while True:
                    message = await receive()
                    if message["type"] == "http.disconnect":
                        return None
                    if len(body) + len(message.get("body", b"")) > self.max_body_bytes:
                        raise ValueError("Request too large")
                    body.extend(message.get("body", b""))
                    if not message.get("more_body", False):
                        return bytes(body)

            try:
                body = await asyncio.wait_for(read_body(), timeout=BODY_TIMEOUT_SECONDS)
            except asyncio.TimeoutError:
                return await reject(408, "Request body read timed out.")
            except ValueError:
                return await reject(413, "Request body exceeds the configured limit.")
            if body is None:
                return
            delivered = False

            async def replay():
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {"type": "http.request", "body": body, "more_body": False}
                return await receive()

            return await self.app(scope, replay, send)
        finally:
            self.lock.release()


class Question(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1)


def create_app(mentor: Mentor) -> FastAPI:
    app = FastAPI(title="Private-source Investment Mentor", docs_url="/docs")
    # JSON may encode one non-BMP character as two six-byte Unicode escapes.
    app.add_middleware(RequestBoundary, max_body_bytes=12 * mentor.settings["max_query_chars"] + 1024)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]"])

    @app.get("/health")
    def health():
        try:
            mentor.validate_release()
        except ValueError:
            raise HTTPException(503, "Reviewed knowledge unavailable; verify locally and restart.") from None
        return {"status": "ready", "case_count": len(mentor.index.cases),
                "principle_count": len(mentor.index.principles), "live_data": False,
                "release_sha256": mentor.release_hash, "provider_verified": False,
                "max_query_chars": mentor.settings["max_query_chars"]}

    @app.post("/ask")
    def ask(body: Question):
        if not body.query.strip() or len(body.query) > mentor.settings["max_query_chars"]:
            raise HTTPException(422, "Query must be nonempty and within the configured length limit.")
        try:
            result = mentor.ask(body.query)
            return {k: result[k] for k in ("route", "answer", "citations", "guards", "release_sha256")}
        except (ValueError, OSError, KeyError):
            raise HTTPException(503, "Reviewed knowledge/configuration unavailable; inspect locally.") from None

    return app
