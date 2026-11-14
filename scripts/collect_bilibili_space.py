"""
P2A-4 B站主页/空间候选列表枚举(CLI)。
对一个已存在的 batch,用 yt-dlp --flat-playlist 枚举 UP 主空间公开视频候选(浅 metadata),
写入 batch/collect_report.json。
- 仅枚举:不下载视频、不下载封面、不逐条深抓、不转写、不卡片化、不 approve
- 默认 max_items 取 admin_settings.ingest.bilibili_max_items(默认 20),硬上限 100
- yt-dlp 缺失 / 不支持 / 无候选 / 不可访问 → report.status=failed + error,退出码非 0
- 不使用 cookies,不绕过平台风控,不破解 DRM,不枚举私密/付费/会员内容

用法:
  python scripts/collect_bilibili_space.py --batch <batch_id> --url https://space.bilibili.com/<mid>
  python scripts/collect_bilibili_space.py --batch <batch_id> --url <space_url> --max-items 20
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.mentor_ingest_service import load_manifest  # noqa: E402
from services.platform_collect_service import (  # noqa: E402
    collect_bilibili_space,
    DEFAULT_MAX_ITEMS,
)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="P2A-4 B站空间候选枚举")
    ap.add_argument("--batch", required=True)
    ap.add_argument("--url", required=True)
    ap.add_argument("--max-items", type=int, default=None, dest="max_items")
    args = ap.parse_args(argv)

    if not load_manifest(args.batch):
        print(f"[ERROR] batch 不存在: {args.batch}")
        return 2

    res = collect_bilibili_space(args.batch, args.url, max_items=args.max_items)
    if res.get("ok"):
        print(f"[OK] candidates={res['candidate_count']} total_seen={res['total_seen']} "
              f"truncated={res['truncated']}")
        print(f"[REPORT] {res['report_path']}")
        return 0

    err = res.get("error") or {}
    code = err.get("code", "") if isinstance(err, dict) else ""
    msg = err.get("message", "") if isinstance(err, dict) else str(err)
    print(f"[FAILED] code={code} msg={msg}")
    print(f"[REPORT] {res.get('report_path','')}")
    print("[FALLBACK] 枚举失败时,可手动收集公开视频 URL 用单 URL 采集,或本地导入兜底。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
