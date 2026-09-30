from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from app.dashboard_data import build_dashboard, load_records

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 9, 30, 4, 0, 30, tzinfo=timezone.utc)
CONFIG = yaml.safe_load((ROOT / "config" / "dashboard.yaml").read_text(encoding="utf-8"))["dashboard"]


def _record(minutes_ago: float, event: str, **fields) -> dict:
    ts = NOW - timedelta(minutes=minutes_ago)
    return {"_ts": ts, "ts": ts.isoformat(), "event": event, "service": "api", **fields}


def _sent(minutes_ago: float, latency: int, cost: float = 0.002, quality: float = 0.9, **extra) -> dict:
    return _record(
        minutes_ago,
        "response_sent",
        latency_ms=latency,
        ttft_ms=50,
        tokens_in=30,
        tokens_out=100,
        cost_usd=cost,
        quality_score=quality,
        tool_success=True,
        **extra,
    )


def _build(records: list[dict], **kwargs) -> dict:
    return build_dashboard(records, CONFIG, now=NOW, **kwargs)


def test_dashboard_has_the_six_contract_panels_and_a_60_minute_window() -> None:
    data = _build([])

    assert list(data["panels"]) == ["latency", "traffic", "errors", "cost", "tokens", "quality"]
    assert len(data["minutes"]) == 60
    assert data["minutes"][-1].startswith("2026-09-30T04:00:00")
    for panel in data["panels"].values():
        assert len(panel["buckets"]) == 60
        assert panel["threshold"]["status"] == "no_data"


def test_latency_percentiles_ttft_and_threshold_breach() -> None:
    records = [_sent(1, latency) for latency in (100, 200, 300, 400, 5000)]

    latency = _build(records)["panels"]["latency"]

    assert latency["tiles"]["p50"] == 300
    assert latency["tiles"]["p95"] == latency["tiles"]["p99"] == 5000
    assert latency["tiles"]["ttft_p95"] == 50
    assert latency["threshold"]["status"] == "breach"
    assert latency["buckets"][-2]["n"] == 5 and latency["buckets"][-1]["n"] == 0


def test_records_outside_the_window_are_ignored() -> None:
    old = _sent(61, 9999)
    future = _sent(-5, 9999)

    data = _build([old, future, _sent(2, 250)])

    assert data["window"]["records"] == 1
    assert data["panels"]["latency"]["tiles"]["p95"] == 250
    assert data["panels"]["latency"]["threshold"]["status"] == "ok"


def test_error_rate_retrieval_success_and_breakdown() -> None:
    records = [_record(2, "request_received") for _ in range(4)]
    records += [_sent(2, 300) for _ in range(3)]
    records.append(_record(2, "request_failed", error_type="RuntimeError", tool_success=False))

    errors = _build(records, guardrails={"retrieval_success_rate_pct_min": 90})["panels"]["errors"]

    assert errors["tiles"]["error_rate_pct"] == 25.0
    assert errors["tiles"]["tool_success_rate_pct"] == 75.0
    assert errors["breakdown"] == {"RuntimeError": 1}
    assert errors["threshold"]["status"] == "breach"
    assert errors["secondary_threshold"]["status"] == "breach"


def test_traffic_rate_uses_active_span_with_one_minute_floor() -> None:
    burst = [_record(3 - i * 0.01, "request_received") for i in range(10)]

    traffic = _build(burst)["panels"]["traffic"]

    assert traffic["tiles"] == {"requests": 10, "rate_per_minute": 10.0}
    assert traffic["threshold"]["status"] == "ok"
    assert sum(b["requests"] for b in traffic["buckets"]) == 10


def test_cost_tokens_and_quality_aggregates() -> None:
    records = [_sent(1, 300, cost=0.01, quality=0.5), _sent(1, 300, cost=0.02, quality=0.7)]

    panels = _build(records)["panels"]

    assert panels["cost"]["tiles"]["total"] == 0.03
    assert panels["cost"]["threshold"]["status"] == "ok"
    assert panels["tokens"]["tiles"] == {"tokens_in": 60, "tokens_out": 200}
    assert panels["tokens"]["buckets"][-2] == {"tokens_in": 60, "tokens_out": 200}
    assert panels["quality"]["tiles"]["mean"] == 0.6
    assert panels["quality"]["threshold"]["status"] == "breach"


def test_load_records_skips_corrupt_lines_and_records_without_timestamp(tmp_path: Path) -> None:
    log = tmp_path / "logs.jsonl"
    log.write_text(
        "\n".join(
            [
                json.dumps({"ts": "2026-09-30T03:23:36.986197Z", "event": "response_sent"}),
                "not json",
                json.dumps({"event": "no_ts"}),
                json.dumps({"ts": "garbage", "event": "bad_ts"}),
                "",
            ]
        ),
        encoding="utf-8",
    )

    records = load_records(log)

    assert [r["event"] for r in records] == ["response_sent"]
    assert records[0]["_ts"].tzinfo is not None
    assert load_records(tmp_path / "missing.jsonl") == []
