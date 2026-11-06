"""Loopback-only HTTP serving; no authentication or public deployment is implied."""
from __future__ import annotations

import threading

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .runtime import Mentor


class Question(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=2000)


def create_app(mentor: Mentor) -> FastAPI:
    app = FastAPI(title="Private-source Investment Mentor", docs_url="/docs")
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]"])
    lock = threading.Lock()

    @app.get("/health")
    def health():
        return {"status": "ready", "case_count": len(mentor.index.cases),
                "principle_count": len(mentor.index.principles), "live_data": False}

    @app.post("/ask")
    def ask(body: Question):
        if not lock.acquire(blocking=False):
            raise HTTPException(429, "A request is already running; retry after it completes.")
        try:
            result = mentor.ask(body.query)
            return {k: result[k] for k in ("route", "answer", "citations", "guards")}
        except (ValueError, OSError, KeyError):
            raise HTTPException(503, "Reviewed knowledge/configuration unavailable; inspect locally.") from None
        finally:
            lock.release()

    return app
