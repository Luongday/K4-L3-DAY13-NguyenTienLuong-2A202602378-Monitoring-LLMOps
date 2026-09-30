from __future__ import annotations

from pathlib import Path

from scripts.check_report import check_report


def _report(tmp_path: Path, body: str) -> Path:
    submission = tmp_path / "submission"
    (submission / "evidence").mkdir(parents=True)
    (submission / "evidence" / "ok.png").write_bytes(b"png")
    report = submission / "REPORT.md"
    report.write_text(body, encoding="utf-8")
    return report


def test_complete_report_has_no_errors(tmp_path: Path) -> None:
    report = _report(tmp_path, "- **Commit SHA cuối:** abc1234\n![ok](evidence/ok.png)\nSee `evidence/ok.png`.\n")

    assert check_report(report) == ([], [])


def test_reports_missing_files_empty_fields_placeholders_and_machine_paths(tmp_path: Path) -> None:
    report = _report(
        tmp_path,
        "- **Commit SHA cuối:**\n"
        "![x](evidence/missing.png)\n"
        "ID: req-XXXXXXXX\n"
        "Ảnh ở C:\\Users\\me\\shot.png\n"
        "> hint with `evidence/not-checked.png` and req-XXXXXXXX\n"
        "- [ ] chưa tick\n",
    )

    errors, warnings = check_report(report)

    joined = "\n".join(errors)
    assert "mục chưa điền" in joined
    assert "evidence/missing.png" in joined
    assert "placeholder" in joined
    assert "đường dẫn máy" in joined
    assert "not-checked" not in joined
    assert len(warnings) == 1
