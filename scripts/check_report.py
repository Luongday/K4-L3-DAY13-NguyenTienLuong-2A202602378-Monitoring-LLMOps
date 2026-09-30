from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.cli import configure_utf8_stdio

MACHINE_PATH = re.compile(r"[A-Za-z]:\\|/home/|/Users/|/tmp/")
PLACEHOLDER = re.compile(r"req-X{8}|<MSSV>|\bTODO\b")
EMPTY_FIELD = re.compile(r"^- \*\*[^*]+:\*\*\s*$")
MARKDOWN_TARGET = re.compile(r"\]\(([^)\s]+)\)")
EVIDENCE_REF = re.compile(r"`(evidence/[^`\s]+)`")


def check_report(report: Path) -> tuple[list[str], list[str]]:
    """Return (errors, warnings) for a submission report."""
    errors: list[str] = []
    warnings: list[str] = []
    lines = report.read_text(encoding="utf-8").splitlines()
    submission_dir = report.parent
    for number, line in enumerate(lines, start=1):
        if line.startswith(">"):  # template hints, not submitted content
            continue
        where = f"REPORT.md:{number}"
        if MACHINE_PATH.search(line):
            errors.append(f"{where}: đường dẫn máy cục bộ: {line.strip()[:80]}")
        if PLACEHOLDER.search(line):
            errors.append(f"{where}: còn placeholder: {line.strip()[:80]}")
        if EMPTY_FIELD.match(line):
            errors.append(f"{where}: mục chưa điền: {line.strip()}")
        if line.startswith("- [ ]"):
            warnings.append(f"{where}: checklist chưa tick: {line.strip()[6:70]}")
        targets = [t for t in MARKDOWN_TARGET.findall(line) if not t.startswith(("http", "#", "mailto:"))]
        targets += EVIDENCE_REF.findall(line)
        for target in targets:
            path = target.split("#", 1)[0]
            if path and not (submission_dir / path).exists():
                errors.append(f"{where}: file không tồn tại: {path}")
    return errors, warnings


def main() -> int:
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(description="Kiểm tra submission/REPORT.md trước khi nộp")
    parser.add_argument("--report", type=Path, default=REPO_ROOT / "submission" / "REPORT.md")
    args = parser.parse_args()

    errors, warnings = check_report(args.report)
    for message in errors:
        print(f"LỖI  {message}")
    for message in warnings:
        print(f"NHẮC {message}")
    print(f"\n{len(errors)} lỗi, {len(warnings)} nhắc nhở")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
