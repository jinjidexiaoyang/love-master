"""
P4C 创建采集批次(CLI)。
仅创建 batch + 扫描 source_items,不做转写、不入库。

用法:
  # 本地文件夹扫描
  python scripts/create_mentor_ingest_batch.py local --path "D:/raw/mentor_clips" \
      --target-mode merge_existing_mentor --real-mentor 张三 --public-name 灰狼 \
      --style-label 约会节奏控制型

  # 单 URL 登记(不主动下载)
  python scripts/create_mentor_ingest_batch.py url --url https://... --real-mentor 张三

  # 抖音/B站主页登记(默认登记 URL + fallback 提示)
  python scripts/create_mentor_ingest_batch.py douyin   --url https://www.douyin.com/user/...
  python scripts/create_mentor_ingest_batch.py bilibili --url https://space.bilibili.com/...
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.mentor_ingest_service import (  # noqa: E402
    create_batch,
    scan_local_folder,
    collect_single_url,
    collect_douyin_homepage,
    collect_bilibili_homepage,
    write_ingest_report,
)


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--target-mode", default="archive_only",
                        choices=["new_mentor", "merge_existing_mentor",
                                 "merge_existing_course", "archive_only"])
    parser.add_argument("--real-mentor", default="", dest="real_mentor")
    parser.add_argument("--public-mentor-id", default="")
    parser.add_argument("--public-name", default="")
    parser.add_argument("--style-label", default="")
    parser.add_argument("--target-course", default="")
    parser.add_argument("--label", default="")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="创建导师采集批次(P4C)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_local = sub.add_parser("local", help="本地文件夹扫描")
    p_local.add_argument("--path", required=True)
    _common(p_local)

    p_url = sub.add_parser("url", help="登记单视频链接(不下载)")
    p_url.add_argument("--url", required=True)
    _common(p_url)

    p_dy = sub.add_parser("douyin", help="登记抖音主页(优雅占位)")
    p_dy.add_argument("--url", required=True)
    _common(p_dy)

    p_bi = sub.add_parser("bilibili", help="登记 B 站主页(优雅占位)")
    p_bi.add_argument("--url", required=True)
    _common(p_bi)

    args = ap.parse_args(argv)

    common_kwargs = dict(
        target_mode=args.target_mode,
        real_mentor_name=args.real_mentor,
        public_mentor_id=args.public_mentor_id,
        public_name=args.public_name,
        style_label=args.style_label,
        target_course=args.target_course,
        label=args.label,
    )

    if args.cmd == "local":
        m = create_batch("local_folder", local_path=str(args.path), **common_kwargs)
        result = scan_local_folder(m["batch_id"], Path(args.path))
        write_ingest_report(m["batch_id"], {"phase": "scan", **result})
        print(f"[OK] batch_id = {m['batch_id']}")
        print(f"[OK] {result}")
        return 0 if result.get("ok") else 1

    if args.cmd == "url":
        m = create_batch("single_url", source_url=args.url, **common_kwargs)
        result = collect_single_url(m["batch_id"], args.url)
        write_ingest_report(m["batch_id"], {"phase": "scan", **result})
        print(f"[OK] batch_id = {m['batch_id']}")
        print(f"[OK] {result}")
        return 0 if result.get("ok") else 1

    if args.cmd == "douyin":
        m = create_batch("douyin_homepage", source_url=args.url, **common_kwargs)
        result = collect_douyin_homepage(m["batch_id"], args.url)
        write_ingest_report(m["batch_id"], {"phase": "scan", **result})
        print(f"[OK] batch_id = {m['batch_id']}")
        print(f"[INFO] {result}")
        return 0  # 优雅失败也返回 0,batch 已创建

    if args.cmd == "bilibili":
        m = create_batch("bilibili_homepage", source_url=args.url, **common_kwargs)
        result = collect_bilibili_homepage(m["batch_id"], args.url)
        write_ingest_report(m["batch_id"], {"phase": "scan", **result})
        print(f"[OK] batch_id = {m['batch_id']}")
        print(f"[INFO] {result}")
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
