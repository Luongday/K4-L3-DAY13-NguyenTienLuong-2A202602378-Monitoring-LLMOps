from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.cli import configure_utf8_stdio
from app.dashboard_data import build_dashboard, latest_timestamp, load_records, parse_ts
from scripts.validate_dashboard import DashboardConfigError, load_dashboard_config

TEMPLATE = REPO_ROOT / "scripts" / "dashboard.html"
DASHBOARD_CONFIG = REPO_ROOT / "config" / "dashboard.yaml"
SLO_CONFIG = REPO_ROOT / "config" / "slo.yaml"
EMBED_MARKER = "/*__EMBEDDED__*/null"


def resolve_now(option: str | None, records: list[dict]) -> datetime:
    if option is None:
        return datetime.now(timezone.utc)
    if option == "latest":
        return latest_timestamp(records) or datetime.now(timezone.utc)
    parsed = parse_ts(option)
    if parsed is None:
        raise SystemExit(f"--now phải là 'latest' hoặc timestamp ISO-8601, nhận được: {option}")
    return parsed


def compute(log_path: Path, now_option: str | None) -> dict:
    dashboard = load_dashboard_config(DASHBOARD_CONFIG)["dashboard"]
    guardrails = yaml.safe_load(SLO_CONFIG.read_text(encoding="utf-8")).get("guardrails", {})
    records = load_records(log_path)
    return build_dashboard(records, dashboard, now=resolve_now(now_option, records), guardrails=guardrails)


def render_snapshot(data: dict) -> str:
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    return TEMPLATE.read_text(encoding="utf-8").replace(EMBED_MARKER, payload)


def make_handler(log_path: Path, now_option: str | None) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - http.server API
            route = self.path.split("?", 1)[0]
            if route == "/data.json":
                body = json.dumps(compute(log_path, now_option), ensure_ascii=False).encode("utf-8")
                self._send(200, "application/json; charset=utf-8", body)
            elif route in ("/", "/index.html"):
                self._send(200, "text/html; charset=utf-8", TEMPLATE.read_bytes())
            else:
                self._send(404, "text/plain; charset=utf-8", b"not found")

        def _send(self, status: int, content_type: str, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args) -> None:
            return None

    return Handler


def main() -> int:
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(description="Dashboard 6 panel đọc data/logs.jsonl theo config/dashboard.yaml")
    parser.add_argument("--logs", type=Path, default=REPO_ROOT / "data" / "logs.jsonl")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8050)
    parser.add_argument(
        "--now",
        help="Mốc cuối của cửa sổ thời gian: 'latest' (record mới nhất) hoặc ISO-8601. Mặc định: giờ hiện tại.",
    )
    parser.add_argument("--snapshot", type=Path, help="Ghi một file HTML tĩnh rồi thoát, không chạy server.")
    args = parser.parse_args()

    try:
        data = compute(args.logs, args.now)
    except DashboardConfigError as exc:
        print(f"KHÔNG HỢP LỆ: {exc}")
        return 1

    if args.snapshot:
        args.snapshot.write_text(render_snapshot(data), encoding="utf-8")
        print(f"Đã ghi snapshot: {args.snapshot} ({data['window']['records']} log record trong cửa sổ)")
        return 0

    server = ThreadingHTTPServer((args.host, args.port), make_handler(args.logs, args.now))
    print(f"Dashboard: http://{args.host}:{args.port}  (đọc {args.logs}, Ctrl+C để dừng)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
