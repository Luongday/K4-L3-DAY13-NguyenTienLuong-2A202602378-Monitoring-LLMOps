from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import httpx

from app import logging_config
from app.logging_config import scrub_event
from app.main import app

CHAT_BODY = {
    "user_id": "student-01",
    "session_id": "session-01",
    "feature": "qa",
    "message": "Explain observability",
}


def _post_chat(headers: dict[str, str] | None = None, body: dict | None = None) -> list[httpx.Response]:
    async def send() -> list[httpx.Response]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            first = await client.post("/chat", json=body or CHAT_BODY, headers=headers)
            second = await client.post("/chat", json=CHAT_BODY)
            return [first, second]

    return asyncio.run(send())


def _read_logs(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_generated_correlation_id_uses_req_hex_format_and_response_headers(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(logging_config, "LOG_PATH", tmp_path / "logs.jsonl")

    first, second = _post_chat()

    for response in (first, second):
        assert re.fullmatch(r"req-[0-9a-f]{8}", response.headers["x-request-id"])
        assert response.headers["x-request-id"] == response.json()["correlation_id"]
        assert int(response.headers["x-response-time-ms"]) >= 0
    assert first.headers["x-request-id"] != second.headers["x-request-id"]


def test_incoming_request_id_is_reused_in_logs_and_response(monkeypatch, tmp_path: Path) -> None:
    log_path = tmp_path / "logs.jsonl"
    monkeypatch.setattr(logging_config, "LOG_PATH", log_path)

    first, second = _post_chat(headers={"x-request-id": "req-abcdef12"})

    assert first.headers["x-request-id"] == "req-abcdef12"
    assert second.headers["x-request-id"] != "req-abcdef12"
    api_logs = [rec for rec in _read_logs(log_path) if rec["service"] == "api"]
    first_logs = [rec for rec in api_logs if rec["correlation_id"] == "req-abcdef12"]
    assert {rec["event"] for rec in first_logs} == {"request_received", "response_sent"}
    assert len({rec["correlation_id"] for rec in api_logs}) == 2


def test_unsafe_request_id_is_replaced(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(logging_config, "LOG_PATH", tmp_path / "logs.jsonl")

    for unsafe in ("0987654321", "student@vinuni.edu.vn", "bad id\twith space", "x" * 65):
        first, _ = _post_chat(headers={"x-request-id": unsafe})
        assert re.fullmatch(r"req-[0-9a-f]{8}", first.headers["x-request-id"])


def test_api_logs_carry_request_context_without_leaking_between_requests(
    monkeypatch, tmp_path: Path
) -> None:
    log_path = tmp_path / "logs.jsonl"
    monkeypatch.setattr(logging_config, "LOG_PATH", log_path)
    other_body = {**CHAT_BODY, "user_id": "student-02", "session_id": "session-02", "feature": "summary"}

    _post_chat(body=other_body)

    api_logs = [rec for rec in _read_logs(log_path) if rec["service"] == "api"]
    by_session = {rec["session_id"]: rec for rec in api_logs}
    assert set(by_session) == {"session-01", "session-02"}
    for rec in api_logs:
        assert {"correlation_id", "user_id_hash", "session_id", "feature", "model", "env"} <= rec.keys()
        assert rec["correlation_id"] != "MISSING"
        assert "student-0" not in json.dumps(rec)
    assert by_session["session-02"]["feature"] == "summary"
    assert by_session["session-01"]["feature"] == "qa"


def test_pii_in_message_is_scrubbed_before_it_is_written(monkeypatch, tmp_path: Path) -> None:
    log_path = tmp_path / "logs.jsonl"
    monkeypatch.setattr(logging_config, "LOG_PATH", log_path)
    message = "Email student@vinuni.edu.vn phone 0987654321 CCCD 012345678901 card 4111 1111 1111 1111"

    _post_chat(body={**CHAT_BODY, "message": message})

    raw = log_path.read_text(encoding="utf-8")
    for pii in ("student@vinuni.edu.vn", "0987654321", "012345678901", "4111 1111 1111 1111"):
        assert pii not in raw
    assert "REDACTED_EMAIL" in raw


def test_scrub_event_covers_top_level_nested_and_exception_fields() -> None:
    event = {
        "ts": "2026-09-30T03:17:08.998988Z",
        "event": "request_failed",
        "user_id_hash": "123456789012",
        "error_type": "call 0987654321",
        "exception": "Traceback ... student@vinuni.edu.vn",
        "payload": {"detail": "card 4111-1111-1111-1111", "nested": {"who": ["a@b.co"]}, "n": 3},
    }

    out = scrub_event(None, "error", event)

    assert out["ts"] == event["ts"]
    assert out["user_id_hash"] == "123456789012"
    assert out["error_type"] == "call [REDACTED_PHONE_VN]"
    assert "student@" not in out["exception"]
    assert out["payload"]["detail"] == "card [REDACTED_CREDIT_CARD]"
    assert out["payload"]["nested"]["who"] == ["[REDACTED_EMAIL]"]
    assert out["payload"]["n"] == 3


def test_failed_request_keeps_correlation_id_header_and_log_context(monkeypatch, tmp_path: Path) -> None:
    from app.incidents import STATE

    log_path = tmp_path / "logs.jsonl"
    monkeypatch.setattr(logging_config, "LOG_PATH", log_path)
    STATE["tool_fail"] = True
    try:
        first, second = _post_chat(headers={"x-request-id": "req-fa11ed01"})
    finally:
        STATE["tool_fail"] = False

    assert first.status_code == 500 and second.status_code == 500
    assert first.headers["x-request-id"] == "req-fa11ed01"
    assert re.fullmatch(r"req-[0-9a-f]{8}", second.headers["x-request-id"])
    failed = [rec for rec in _read_logs(log_path) if rec["event"] == "request_failed"]
    assert {rec["correlation_id"] for rec in failed} == {
        "req-fa11ed01",
        second.headers["x-request-id"],
    }
    for rec in failed:
        assert rec["error_type"] == "RuntimeError"
        assert rec["tool_name"] == "retrieval" and rec["tool_success"] is False
        assert {"user_id_hash", "session_id", "feature", "model", "env"} <= rec.keys()
