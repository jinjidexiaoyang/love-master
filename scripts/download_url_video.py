"""
P2A-2 单视频下载(CLI)。
对一个 batch 内已有的 source_item(由 P2A-1 metadata 采集生成)下载视频到 batch/videos/。
- 仅下载视频:不下载封面、不抽音频、不转写、不卡片化、不 approve
- 下载成功回填 local_video_path / file_hash(sha256),status=downloaded
- yt-dlp 缺失 / 不可访问 / 输出缺失 / 重复 → 写结构化 error,退出码非 0(duplicate 视为成功跳过)
- 不使用 cookies,不绕过平台风控,不破解 DRM,不处理私密/付费/会员内容

用法:
  python scripts/download_url_video.py --batch <batch_id> --item <item_id>
  python scripts/download_url_video.py --batch <batch_id> --item <item_id> --force
  python scripts/download_url_video.py --batch <batch_id> --item <item_id> --quality "best[height<=720]/best"
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
    download_video_item,
    DEFAULT_QUALITY,
)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="P2A-2 单视频下载")
    ap.add_argument("--batch", required=True)
    ap.add_argument("--item", required=True)
    ap.add_argument("--quality", default=DEFAULT_QUALITY)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)

    if not load_manifest(args.batch):
        print(f"[ERROR] batch 不存在: {args.batch}")
        return 2

    res = download_video_item(args.batch, args.item,
                              quality=args.quality, force=args.force)
    log = res.get("log")
    if log:
        print("--- yt-dlp log tail ---")
        print(log)
        print("--- end log ---")

    if res.get("ok"):
        if res.get("skipped") or res.get("status") == "skipped":
            reason = res.get("reason") or "duplicate"
            print(f"[SKIP] item={res['item_id']} status={res.get('status')} reason={reason} "
                  f"path={res.get('local_video_path','')}")
            return 0
        if res.get("warning"):
            print(f"[WARN] {res['warning']}")
        print(f"[OK] item={res['item_id']} status=downloaded "
              f"path={res.get('local_video_path','')} hash={res.get('file_hash','')}")
        return 0

    err = res.get("error") or {}
    code = err.get("code", "") if isinstance(err, dict) else ""
    msg = err.get("message", "") if isinstance(err, dict) else str(err)
    print(f"[FAILED] code={code} msg={msg}")
    print("[FALLBACK] 下载失败时,可手动下载公开视频到 raw_input/local 后改走本地导入。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
