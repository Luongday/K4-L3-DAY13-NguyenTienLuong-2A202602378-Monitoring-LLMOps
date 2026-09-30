from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = ("name", "severity", "condition", "duration", "type", "channel", "owner", "runbook")


def _alerts() -> list[dict]:
    return yaml.safe_load((ROOT / "config" / "alert_rules.yaml").read_text(encoding="utf-8"))["alerts"]


def test_three_complete_symptom_based_alerts_without_todo_placeholders() -> None:
    alerts = _alerts()

    assert len(alerts) == 3
    assert len({alert["name"] for alert in alerts}) == 3
    for alert in alerts:
        for field in REQUIRED:
            assert alert.get(field), f"{alert.get('name')}: missing {field}"
        assert "TODO" not in str(alert)
        assert alert["type"] == "symptom-based"
        assert alert["channel"] == "slack" and alert["slack_channel"].startswith("#")
        assert alert["severity"] in {"info", "warning", "critical"}


def test_each_runbook_anchor_points_to_a_filled_section_in_alerts_doc() -> None:
    doc = (ROOT / "docs" / "alerts.md").read_text(encoding="utf-8")
    sections = {part.split("\n", 1)[0].strip(): part for part in doc.split("## ")[1:]}

    for alert in _alerts():
        path, anchor = alert["runbook"].split("#")
        assert path == "docs/alerts.md"
        section = sections[anchor.replace("-", " ").capitalize()]
        assert alert["name"] in section
        assert "Tên:\n" not in section and "Mitigation tạm thời:\n" not in section
        assert alert["duration"] in section


def test_slo_error_budget_matches_target() -> None:
    slo = yaml.safe_load((ROOT / "config" / "slo.yaml").read_text(encoding="utf-8"))["primary_slo"]

    assert round(100 - slo["target_percent"], 3) == slo["error_budget_percent"]
    assert slo["rationale"] and slo["error_budget_example"]
