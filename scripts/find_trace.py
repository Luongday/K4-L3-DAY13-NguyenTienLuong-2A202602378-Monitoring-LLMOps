from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv

from app.cli import configure_utf8_stdio


def main() -> int:
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(description="Tìm trace Langfuse theo correlation_id (x-request-id)")
    parser.add_argument("correlation_id", help="ví dụ req-1a2b3c4d")
    parser.add_argument("--hours", type=float, default=6, help="tìm trong N giờ gần nhất (mặc định 6)")
    args = parser.parse_args()

    load_dotenv(REPO_ROOT / ".env")
    from langfuse import get_client

    client = get_client()
    now = datetime.now(timezone.utc)
    result = client.api.observations.get_many(
        from_start_time=now - timedelta(hours=args.hours),
        to_start_time=now + timedelta(minutes=1),
        limit=1000,
        fields="core,basic,metadata",
    )
    roots = [
        o for o in result.data
        if o.type == "AGENT" and (o.metadata or {}).get("correlation_id") == args.correlation_id
    ]
    if not roots:
        print(f"Chưa thấy trace cho {args.correlation_id}. Đợi vài giây cho trace được gửi lên rồi chạy lại.")
        return 1
    project_id = None
    try:
        project_id = client.api.projects.get().data[0].id
    except Exception:
        pass
    for root in sorted(roots, key=lambda o: o.start_time):
        local = root.start_time.astimezone(timezone(timedelta(hours=7)))
        print(f"trace_id : {root.trace_id}")
        print(f"thời gian: {root.start_time:%Y-%m-%d %H:%M:%S} UTC = {local:%H:%M:%S} giờ Việt Nam")
        if project_id:
            print(f"link     : https://cloud.langfuse.com/project/{project_id}/traces/{root.trace_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
