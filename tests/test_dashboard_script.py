from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from scripts import dashboard


def _write_logs(path: Path) -> None:
    ts = datetime(2026, 9, 30, 4, 0, 5, tzinfo=timezone.utc).isoformat()
    records = [
        {"ts": ts, "event": "request_received", "service": "api", "payload": {"message_preview": "</script><b>x"}},
        {"ts": ts, "event": "response_sent", "service": "api", "latency_ms": 120, "ttft_ms": 50,
         "tokens_in": 20, "tokens_out": 90, "cost_usd": 0.002, "quality_score": 0.9, "tool_success": True},
    ]
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")


def test_compute_uses_latest_record_as_window_end(tmp_path: Path) -> None:
    log = tmp_path / "logs.jsonl"
    _write_logs(log)

    data = dashboard.compute(log, "latest")

    assert data["window"]["records"] == 2
    assert data["panels"]["latency"]["tiles"]["p95"] == 120
    assert data["panels"]["quality"]["threshold"]["status"] == "ok"


def test_snapshot_embeds_data_and_cannot_break_out_of_the_script_tag(tmp_path: Path) -> None:
    log = tmp_path / "logs.jsonl"
    _write_logs(log)
    data = dashboard.compute(log, "latest")

    html = dashboard.render_snapshot(data)

    assert dashboard.EMBED_MARKER not in html
    assert '"panels"' in html
    scripts = re.findall(r"<script>(.*?)</script>", html, re.S)
    assert len(scripts) == 1 and "</script" not in scripts[0]


def test_invalid_now_option_is_rejected(tmp_path: Path) -> None:
    import pytest

    with pytest.raises(SystemExit):
        dashboard.resolve_now("not-a-timestamp", [])
