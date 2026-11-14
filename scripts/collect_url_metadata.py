"""
P2A-1 单 URL metadata 采集(CLI)。
对一个已存在的 batch,用 yt-dlp 抓取单个视频 URL 的 metadata 并写入 source_items。
- 仅 metadata:不下载视频、不下载封面、不抽音频、不转写、不卡片化、不 approve
- yt-dlp 缺失 / URL 不支持 / 不可访问 → 写 failed source_item + 结构化 error,退出码非 0
- 不使用 cookies,不绕过平台风控,不破解 DRM

用法:
  python scripts/collect_url_metadata.py --batch <batch_id> --url https://www.bilibili.com/video/BV...
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.mentor_ingest_service import load_manifest  # noqa: E402
from services.platform_collect_service import collect_url_metadata  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="P2A-1 单 URL metadata 采集")
    ap.add_argument("--batch", required=True)
    ap.add_argument("--url", required=True)
    args = ap.parse_args(argv)

    if not load_manifest(args.batch):
        print(f"[ERROR] batch 不存在: {args.batch}")
        return 2

    res = collect_url_metadata(args.batch, args.url)
    if res.get("ok"):
        print(f"[OK] item_id={res['item_id']} platform={res['platform']} "
              f"status={res['status']}")
        print(f"[META] title={res.get('title','')!r} author={res.get('author_name','')!r} "
              f"bv={res.get('platform_item_id','')} duration={res.get('duration_sec',0)}s "
              f"yt-dlp={res.get('yt_dlp_version','')}")
        return 0

    err = res.get("error") or {}
    if isinstance(err, dict):
        print(f"[FAILED] code={err.get('code','')} msg={err.get('message','')}")
    else:
        print(f"[FAILED] {res.get('error')}")
    print("[FALLBACK] 自动采集失败时,可手动下载公开视频到 raw_input/local 后改走本地导入。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
