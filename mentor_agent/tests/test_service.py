import asyncio
import socket
import threading
import time
from pathlib import Path
from unittest.mock import Mock

import pytest
import requests
import uvicorn
from fastapi.testclient import TestClient

from knowledge.tests.test_pipeline import cfg
from mentor_agent.runtime import Mentor
from mentor_agent.service import RequestBoundary, create_app
from mentor_agent.tests.test_runtime import FakeMentor, mentor_setup
from transcription.common import atomic_write_json, read_json


@pytest.fixture
def client():
    mentor = Mock()
    mentor.settings = {"max_query_chars": 20}
    mentor.ask.return_value = {
        "route": "insufficient_evidence", "answer": {}, "citations": [], "guards": [],
        "release_sha256": "a" * 64,
    }
    with TestClient(create_app(mentor), base_url="http://127.0.0.1") as client:
        yield client, mentor


@pytest.mark.parametrize("body", [{"query": " "}, {"query": "x" * 21}, {"query": 1},
                                  {"query": "ok", "extra": True}, {}, {"query": None}])
def test_bad_queries_are_client_errors_without_work(client, body):
    client, mentor = client
    assert client.post("/ask", json=body).status_code == 422
    mentor.ask.assert_not_called()


def test_configured_query_limit_overrides_old_hardcoded_limit():
    mentor = Mock(settings={"max_query_chars": 3000})
    mentor.ask.return_value = {key: [] for key in ("route", "answer", "citations", "guards", "release_sha256")}
    with TestClient(create_app(mentor), base_url="http://127.0.0.1") as client:
        assert client.post("/ask", json={"query": "x" * 2500}).status_code == 200
    mentor.ask.assert_called_once()


def test_oversized_body_and_bad_json_do_not_call_mentor(client):
    client, mentor = client
    response = client.post("/ask", content=b" " * 2000, headers={"content-type": "application/json"})
    assert response.status_code == 413
    response = client.post("/ask", content=b"{", headers={"content-type": "application/json"})
    assert response.status_code == 422
    mentor.ask.assert_not_called()
    assert client.post("/ask", json={"query": "ok"}).status_code == 200


def test_browser_origin_and_content_type_boundary(client):
    client, mentor = client
    assert client.post("/ask", content='{"query":"ok"}').status_code == 415
    for origin in ("null", "https://external.example", "http://localhost:9999"):
        assert client.post("/ask", json={"query": "ok"}, headers={"origin": origin}).status_code == 403
    mentor.ask.assert_not_called()
    assert client.post("/ask", json={"query": "ok"}, headers={"origin": "http://127.0.0.1"}).status_code == 200


def test_busy_request_is_rejected_and_lock_released_after_error(client):
    client, mentor = client
    entered, release = threading.Event(), threading.Event()
    replies = []

    def blocked(query):
        entered.set()
        assert release.wait(5)
        raise ValueError("private failure details")

    mentor.ask.side_effect = blocked
    worker = threading.Thread(target=lambda: replies.append(client.post("/ask", json={"query": "ok"})))
    worker.start()
    try:
        assert entered.wait(5)
        assert client.post("/ask", json={"query": "other"}).status_code == 429
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive()
    assert replies[0].status_code == 503
    assert "private failure details" not in replies[0].text
    mentor.ask.side_effect = None
    assert client.post("/ask", json={"query": "ok"}).status_code == 200


@pytest.mark.parametrize("change", ["release", "semantic_review", "source_review", "raw", "artifact"])
def test_health_and_ask_reject_revocation_or_tampering(mentor_setup, change):
    model = FakeMentor()
    mentor = Mentor(mentor_setup, backend=model)
    release_path = Path(mentor_setup["release"])
    release = read_json(release_path)
    target = {
        "release": release_path, "semantic_review": Path(release["review_path"]),
        "source_review": Path(release["source_config"]["episodes"][0]["review"]),
        "raw": Path(release["source_config"]["episodes"][0]["raw"]),
        "artifact": Path(release["artifacts"][0]["path"]),
    }[change]
    with TestClient(create_app(mentor), base_url="http://127.0.0.1") as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["release_sha256"] == mentor.release_hash
        assert health.json()["provider_verified"] is False
        target.write_text(target.read_text() + "\n")
        assert client.get("/health").status_code == 503
        response = client.post("/ask", json={"query": "evidence wait"})
        assert response.status_code == 503
        assert str(target) not in response.text
        assert not model.calls


def test_malformed_release_is_sanitized_at_loader_boundary(mentor_setup):
    path = Path(mentor_setup["release"])
    atomic_write_json(path, {"schema_version": "knowledge_release.v1", "evidence_authority": "level2",
                             "run_id": "synthetic", "source_config": None})
    with pytest.raises(ValueError, match="Reviewed release"):
        Mentor(mentor_setup, backend=FakeMentor())


def test_streamed_body_limit_timeout_and_disconnect_release_admission(monkeypatch):
    async def scenario():
        accepted = []

        async def inner(scope, receive, send):
            accepted.append(await receive())

        boundary = RequestBoundary(inner, max_body_bytes=4)
        scope = {"type": "http", "method": "POST", "path": "/ask", "scheme": "http",
                 "headers": [(b"host", b"127.0.0.1"), (b"content-type", b"application/json")]}
        sent = []

        async def send(message):
            sent.append(message)

        chunks = iter([{"type": "http.request", "body": b"123", "more_body": True},
                       {"type": "http.request", "body": b"45", "more_body": False}])

        async def streamed():
            return next(chunks)

        await boundary(scope, streamed, send)
        assert sent[0]["status"] == 413
        assert not accepted
        assert not boundary.lock.locked()
        sent.clear()

        async def slow():
            await asyncio.sleep(60)

        monkeypatch.setattr("mentor_agent.service.BODY_TIMEOUT_SECONDS", 0.01)
        await boundary(scope, slow, send)
        assert sent[0]["status"] == 408
        assert not boundary.lock.locked()

        async def disconnected():
            return {"type": "http.disconnect"}

        await boundary(scope, disconnected, send)
        assert not boundary.lock.locked()

        async def complete():
            return {"type": "http.request", "body": b"{}", "more_body": False}

        await boundary(scope, complete, send)
        assert accepted[0]["body"] == b"{}"
        assert not boundary.lock.locked()

    asyncio.run(scenario())


def test_real_loopback_socket_with_mocked_inference(mentor_setup):
    mentor = Mentor(mentor_setup, backend=FakeMentor())
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        server = uvicorn.Server(uvicorn.Config(create_app(mentor), access_log=False, log_level="error"))
        worker = threading.Thread(target=server.run, kwargs={"sockets": [listener]})
        worker.start()
        try:
            deadline = time.monotonic() + 5
            while not server.started and worker.is_alive() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert server.started
            url = f"http://127.0.0.1:{listener.getsockname()[1]}"
            with requests.Session() as client:
                client.trust_env = False
                health = client.get(url + "/health", timeout=5)
                assert health.status_code == 200
                assert health.json()["provider_verified"] is False
                response = client.post(url + "/ask", json={"query": "evidence wait"}, timeout=5)
                assert response.status_code == 200
                assert response.json()["route"] == "principle_assisted"
                assert response.json()["release_sha256"] == mentor.release_hash
                assert client.get(url + "/docs", timeout=5).status_code == 200
        finally:
            server.should_exit = True
            worker.join(5)
        assert not worker.is_alive()
