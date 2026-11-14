"""
P4C 把 batch 内的 transcript 清洗→切块→DeepSeek 卡片化,全部落到 batch staging 目录。
不写入正式 data/mentor_knowledge/。

复用:
  - scripts.process_mentor_videos.clean_text / chunk_text / make_chunk_id
  - scripts.build_mentor_cards.call_deepseek_for_card / build_card / post_process_card

用法:
  python scripts/build_ingest_cards.py --batch <batch_id>
  python scripts/build_ingest_cards.py --batch <batch_id> --sample 20  # 仅卡片化前 20 个 chunk
  python scripts/build_ingest_cards.py --batch <batch_id> --skip-cards # 仅清洗+切块,不调 DeepSeek
  python scripts/build_ingest_cards.py --batch <batch_id> --resume     # 断点续跑
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.process_mentor_videos import (  # noqa: E402
    clean_text, chunk_text, make_chunk_id, safe_slug,
    MIN_CLEAN_CHARS,
)
from scripts.build_mentor_cards import (  # noqa: E402
    call_deepseek_for_card, build_card, post_process_card,
    DEEPSEEK_API_KEY, DEEPSEEK_MODEL,
)
from services.mentor_ingest_service import (  # noqa: E402
    batch_dir, load_manifest, save_manifest, load_source_items,
    set_status, write_ingest_report,
)


def _read_text(path: Path) -> str:
    for enc in ("utf-8", "utf-8-sig", "gb18030", "gbk"):
        try:
            return path.read_text(encoding=enc)
        except UnicodeDecodeError:
            continue
        except Exception:
            return ""
    return ""


def step_clean_chunk(batch_id: str, manifest: dict, items: list[dict]) -> dict:
    bd = batch_dir(batch_id)
    cleaned_dir = bd / "cleaned"
    cleaned_dir.mkdir(parents=True, exist_ok=True)

    target_course = manifest.get("target_course") or manifest.get("public_name") or batch_id
    real_mentor = manifest.get("real_mentor_name") or "unknown"

    chunks: list[dict] = []
    skipped: list[dict] = []
    per_item_stats: list[dict] = []

    for it in items:
        tp = it.get("transcript_path") or ""
        if not tp or not Path(tp).exists():
            skipped.append({"item_id": it["item_id"], "reason": "no transcript"})
            continue

        raw = _read_text(Path(tp))
        if not raw:
            skipped.append({"item_id": it["item_id"], "reason": "decode_failed"})
            continue

        cleaned = clean_text(raw)
        if len(cleaned) < MIN_CLEAN_CHARS:
            skipped.append({"item_id": it["item_id"], "reason": "too_short",
                            "clean_chars": len(cleaned)})
            continue

        cleaned_path = cleaned_dir / f"{it['item_id']}.cleaned.txt"
        cleaned_path.write_text(cleaned, encoding="utf-8")

        pieces = chunk_text(cleaned)
        rel_key = f"{batch_id}/{it['item_id']}"
        item_chunks: list[dict] = []
        for i, t in enumerate(pieces, start=1):
            item_chunks.append({
                "chunk_id": make_chunk_id(rel_key, i),
                "source_type": "mentor_video_ingest",
                "batch_id": batch_id,
                "item_id": it["item_id"],
                "source_file": Path(it.get("local_video_path") or it.get("local_audio_path") or it.get("transcript_path") or "").name,
                "transcript_path": str(tp),
                "mentor": real_mentor,
                "course_name": target_course,
                "title": it.get("title", ""),
                "source_url": it.get("source_url", ""),
                "platform": it.get("platform", ""),
                "chunk_index": i,
                "char_count": len(t),
                "text": t,
            })
        chunks.extend(item_chunks)
        per_item_stats.append({
            "item_id": it["item_id"],
            "clean_chars": len(cleaned),
            "chunks": len(item_chunks),
        })

    chunks_path = bd / "chunks.json"
    chunks_path.write_text(json.dumps(chunks, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "chunks_total": len(chunks),
        "chunks_path": str(chunks_path),
        "items_skipped": skipped,
        "per_item_stats": per_item_stats,
    }


def step_build_cards(batch_id: str, sample: int | None, resume: bool) -> dict:
    bd = batch_dir(batch_id)
    chunks_path = bd / "chunks.json"
    cards_path = bd / "draft_cards.json"
    rejected_path = bd / "rejected_cards.json"
    if not chunks_path.exists():
        return {"ok": False, "error": "chunks.json 不存在,请先做切块"}

    chunks = json.loads(chunks_path.read_text(encoding="utf-8"))
    if not chunks:
        return {"ok": True, "cards_accepted": 0, "cards_rejected": 0, "note": "no chunks"}

    if sample is not None:
        chunks = chunks[: sample]

    accepted: list = []
    rejected: list = []
    if resume:
        if cards_path.exists():
            try:
                accepted = json.loads(cards_path.read_text(encoding="utf-8"))
            except Exception:
                accepted = []
        if rejected_path.exists():
            try:
                rejected = json.loads(rejected_path.read_text(encoding="utf-8"))
            except Exception:
                rejected = []

    done_ids = {c.get("chunk_id") for c in accepted + rejected}

    if not DEEPSEEK_API_KEY:
        return {"ok": False, "error": "DEEPSEEK_API_KEY 未配置"}

    failures = []
    started = time.time()

    for i, ch in enumerate(chunks, 1):
        cid = ch["chunk_id"]
        if cid in done_ids:
            continue
        print(f"[{i}/{len(chunks)}] {cid} ({ch.get('char_count',0)} 字)", flush=True)
        payload, err = call_deepseek_for_card(ch)
        if payload is None:
            failures.append({"chunk_id": cid, "error": err})
            continue
        card = build_card(ch, payload)
        # 兼容 ingest 元字段
        card["batch_id"] = batch_id
        card["item_id"] = ch.get("item_id", "")
        card["source_url"] = ch.get("source_url", "")
        card["platform"] = ch.get("platform", "")
        card["title"] = ch.get("title", "")
        card, _ = post_process_card(card)
        if card["include_in_tutor"]:
            accepted.append(card)
        else:
            rejected.append(card)

        cards_path.write_text(json.dumps(accepted, ensure_ascii=False, indent=2), encoding="utf-8")
        rejected_path.write_text(json.dumps(rejected, ensure_ascii=False, indent=2), encoding="utf-8")

    duration = round(time.time() - started, 1)
    return {
        "ok": True,
        "cards_accepted": len(accepted),
        "cards_rejected": len(rejected),
        "failures": failures,
        "duration_sec": duration,
        "draft_cards_path": str(cards_path),
        "rejected_cards_path": str(rejected_path),
        "model": DEEPSEEK_MODEL,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="P4C 批次清洗+切块+卡片化(staging)")
    ap.add_argument("--batch", required=True)
    ap.add_argument("--sample", type=int, default=None, help="仅卡片化前 N 个 chunk")
    ap.add_argument("--skip-cards", action="store_true", help="仅清洗+切块,不调 DeepSeek")
    ap.add_argument("--resume", action="store_true", help="跳过已处理 chunk_id")
    args = ap.parse_args(argv)

    m = load_manifest(args.batch)
    if not m:
        print(f"[ERROR] batch 不存在: {args.batch}")
        return 2

    items = load_source_items(args.batch)
    items_with_text = [it for it in items if it.get("transcript_path")]
    if not items_with_text:
        print("[INFO] 没有可用 transcript,先跑 transcribe_videos.py 或在导入时直接放 .txt")
        return 1

    set_status(args.batch, "chunking")
    chunk_summary = step_clean_chunk(args.batch, m, items_with_text)
    print(f"[OK] chunks {chunk_summary['chunks_total']} → {chunk_summary['chunks_path']}")

    if args.skip_cards:
        set_status(args.batch, "transcribed", note="chunks 已生成,未卡片化")
        write_ingest_report(args.batch, {"phase": "chunk", **chunk_summary})
        return 0

    cards_summary = step_build_cards(args.batch, args.sample, args.resume)
    if not cards_summary.get("ok"):
        set_status(args.batch, "failed", note=cards_summary.get("error", ""))
        write_ingest_report(args.batch, {"phase": "build_cards", **cards_summary})
        return 1

    m = load_manifest(args.batch)
    m["stats"]["carded"] = cards_summary["cards_accepted"]
    save_manifest(args.batch, m)
    set_status(args.batch, "carded",
               note=f"accepted={cards_summary['cards_accepted']} rejected={cards_summary['cards_rejected']}")
    set_status(args.batch, "review", note="等待管理员审核")

    write_ingest_report(args.batch, {
        "phase": "build_cards",
        "chunk_summary": chunk_summary,
        **cards_summary,
        "finished_at": datetime.now().isoformat(timespec="seconds"),
    })
    print(f"[DONE] cards accepted={cards_summary['cards_accepted']} "
          f"rejected={cards_summary['cards_rejected']} "
          f"({cards_summary['duration_sec']}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
