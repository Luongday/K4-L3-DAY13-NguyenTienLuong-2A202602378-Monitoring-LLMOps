"""Compute dashboard panel data from data/logs.jsonl, following config/dashboard.yaml."""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean
from typing import Any, Callable

from .metrics import percentile

BUCKET = timedelta(minutes=1)


def parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def load_records(path: Path) -> list[dict[str, Any]]:
    """Read JSONL logs, skipping blank/corrupt lines and records without a timestamp."""
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        ts = parse_ts(record.get("ts")) if isinstance(record, dict) else None
        if ts is not None:
            record["_ts"] = ts
            records.append(record)
    return records


def latest_timestamp(records: list[dict[str, Any]]) -> datetime | None:
    return max((r["_ts"] for r in records), default=None)


def _floor_minute(ts: datetime) -> datetime:
    return ts.replace(second=0, microsecond=0)


def _pct(part: int, whole: int) -> float | None:
    return round(part / whole * 100, 2) if whole else None


def _mean(values: list[float], digits: int = 4) -> float | None:
    return round(mean(values), digits) if values else None


def _percentile(values: list[int], p: int) -> float | None:
    return percentile(values, p) if values else None


def _numbers(records: list[dict[str, Any]], field: str) -> list[float]:
    return [r[field] for r in records if isinstance(r.get(field), (int, float)) and not isinstance(r.get(field), bool)]


def _threshold_status(actual: float | None, operator: str, limit: float) -> str:
    if actual is None:
        return "no_data"
    ok = actual <= limit if operator == "lte" else actual >= limit
    return "ok" if ok else "breach"


def _bucket_index(minutes: list[datetime], records: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    slots = {m: [] for m in minutes}
    for record in records:
        key = _floor_minute(record["_ts"])
        if key in slots:
            slots[key].append(record)
    return [slots[m] for m in minutes]


def build_dashboard(
    records: list[dict[str, Any]],
    config: dict[str, Any],
    *,
    now: datetime,
    guardrails: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return everything the dashboard needs: window, per-panel tiles, per-minute buckets, threshold status."""
    guardrails = guardrails or {}
    window_minutes = int(config["time_range_minutes"])
    end_minute = _floor_minute(now)
    minutes = [end_minute - BUCKET * i for i in range(window_minutes - 1, -1, -1)]
    start = minutes[0]
    in_window = [r for r in records if start <= r["_ts"] <= now]
    per_minute = _bucket_index(minutes, in_window)

    builders: dict[str, Callable[..., dict[str, Any]]] = {
        "latency": _latency,
        "traffic": _traffic,
        "errors": _errors,
        "cost": _cost,
        "tokens": _tokens,
        "quality": _quality,
    }
    panels = {}
    for panel in config["panels"]:
        body = builders[panel["id"]](in_window, per_minute, guardrails)
        threshold = dict(panel["threshold"])
        threshold["actual"] = body.pop("threshold_actual")
        threshold["status"] = _threshold_status(threshold["actual"], threshold["operator"], threshold["value"])
        panels[panel["id"]] = {
            "title": panel["title"],
            "unit": panel["unit"],
            "query": panel["query"],
            "threshold": threshold,
            **body,
        }

    return {
        "title": config["title"],
        "generated_at": now.isoformat(),
        "refresh_seconds": config["refresh_seconds"],
        "window": {
            "start": start.isoformat(),
            "end": now.isoformat(),
            "minutes": window_minutes,
            "records": len(in_window),
        },
        "minutes": [m.isoformat() for m in minutes],
        "panels": panels,
    }


def _by_event(records: list[dict[str, Any]], event: str) -> list[dict[str, Any]]:
    return [r for r in records if r.get("event") == event]


def _latency(records, per_minute, _guardrails) -> dict[str, Any]:
    sent = _by_event(records, "response_sent")
    latencies = [int(v) for v in _numbers(sent, "latency_ms")]
    ttfts = [int(v) for v in _numbers(sent, "ttft_ms")]
    buckets = []
    for group in per_minute:
        lat = [int(v) for v in _numbers(_by_event(group, "response_sent"), "latency_ms")]
        ttft = [int(v) for v in _numbers(_by_event(group, "response_sent"), "ttft_ms")]
        buckets.append(
            {
                "p50": _percentile(lat, 50),
                "p95": _percentile(lat, 95),
                "p99": _percentile(lat, 99),
                "ttft_p95": _percentile(ttft, 95),
                "n": len(lat),
            }
        )
    p95 = _percentile(latencies, 95)
    return {
        "tiles": {
            "p50": _percentile(latencies, 50),
            "p95": p95,
            "p99": _percentile(latencies, 99),
            "ttft_p95": _percentile(ttfts, 95),
        },
        "buckets": buckets,
        "threshold_actual": p95,
    }


def _traffic(records, per_minute, _guardrails) -> dict[str, Any]:
    received = _by_event(records, "request_received")
    counts = [len(_by_event(group, "request_received")) for group in per_minute]
    # Average over the active span (first -> last request, at least one minute) so an idle hour
    # does not dilute the rate of a short burst.
    if received:
        span = (max(r["_ts"] for r in received) - min(r["_ts"] for r in received)).total_seconds() / 60
        rate = round(len(received) / max(1.0, span), 2)
    else:
        rate = None
    return {
        "tiles": {"requests": len(received), "rate_per_minute": rate},
        "buckets": [{"requests": c} for c in counts],
        "threshold_actual": rate,
    }


def _tool_success(records: list[dict[str, Any]]) -> tuple[int, int]:
    flags = [r["tool_success"] for r in records if isinstance(r.get("tool_success"), bool)]
    return sum(flags), len(flags)


def _errors(records, per_minute, guardrails) -> dict[str, Any]:
    received = len(_by_event(records, "request_received"))
    failed = _by_event(records, "request_failed")
    ok, total = _tool_success(records)
    retrieval = _pct(ok, total)
    error_rate = _pct(len(failed), received)
    buckets = []
    for group in per_minute:
        group_received = len(_by_event(group, "request_received"))
        group_failed = len(_by_event(group, "request_failed"))
        group_ok, group_total = _tool_success(group)
        buckets.append(
            {
                "error_rate_pct": _pct(group_failed, group_received),
                "failed": group_failed,
                "received": group_received,
                "retrieval_success_pct": _pct(group_ok, group_total),
            }
        )
    minimum = guardrails.get("retrieval_success_rate_pct_min")
    return {
        "tiles": {
            "error_rate_pct": error_rate,
            "tool_success_rate_pct": retrieval,
            "failed": len(failed),
            "received": received,
        },
        "breakdown": dict(Counter(r.get("error_type") or "unknown" for r in failed).most_common()),
        "secondary_threshold": {
            "label": "Retrieval success",
            "aggregation": "tool_success_rate_pct",
            "operator": "gte",
            "value": minimum,
            "actual": retrieval,
            "status": _threshold_status(retrieval, "gte", minimum) if minimum is not None else "no_data",
        },
        "buckets": buckets,
        "threshold_actual": error_rate,
    }


def _cost(records, per_minute, _guardrails) -> dict[str, Any]:
    costs = _numbers(_by_event(records, "response_sent"), "cost_usd")
    total = round(sum(costs), 6) if costs else None
    buckets = []
    for group in per_minute:
        group_costs = _numbers(_by_event(group, "response_sent"), "cost_usd")
        buckets.append({"cost_usd": round(sum(group_costs), 6) if group_costs else None})
    return {
        "tiles": {"total": total, "avg_per_request": _mean(costs, 6), "requests": len(costs)},
        "buckets": buckets,
        "threshold_actual": total,
    }


def _tokens(records, per_minute, _guardrails) -> dict[str, Any]:
    sent = _by_event(records, "response_sent")
    tokens_in = _numbers(sent, "tokens_in")
    tokens_out = _numbers(sent, "tokens_out")
    buckets = []
    for group in per_minute:
        group_sent = _by_event(group, "response_sent")
        group_in = _numbers(group_sent, "tokens_in")
        group_out = _numbers(group_sent, "tokens_out")
        buckets.append(
            {
                "tokens_in": int(sum(group_in)) if group_in else None,
                "tokens_out": int(sum(group_out)) if group_out else None,
            }
        )
    total_in = int(sum(tokens_in)) if tokens_in else None
    total_out = int(sum(tokens_out)) if tokens_out else None
    # Threshold aggregation is sum_by_field: every field total must stay under the limit.
    worst = max((v for v in (total_in, total_out) if v is not None), default=None)
    return {
        "tiles": {"tokens_in": total_in, "tokens_out": total_out},
        "buckets": buckets,
        "threshold_actual": worst,
    }


def _quality(records, per_minute, _guardrails) -> dict[str, Any]:
    scores = _numbers(_by_event(records, "response_sent"), "quality_score")
    average = _mean(scores, 3)
    return {
        "tiles": {"mean": average, "samples": len(scores)},
        "buckets": [
            {"mean": _mean(_numbers(_by_event(group, "response_sent"), "quality_score"), 3)}
            for group in per_minute
        ],
        "threshold_actual": average,
    }
