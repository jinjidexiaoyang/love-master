"""
P4C 批次审核合并(CLI)。
读取 batch staging 内的 chunks.json + draft_cards.json,合并到正式
data/mentor_knowledge/{chunks.json, cards.json}。

合并策略:
  - 备份正式库到 data/mentor_ingest/logs/backup_<ts>/
  - P1 审核闸门:默认只合并 approved_cards.json(管理端逐条审核导出);
      approved_cards.json 不存在 → 拒绝合并;
      待合并数为 0 → 拒绝合并;
      显式 --allow-unreviewed 才回退合并 draft_cards 全量(应急)。
  - reviewed 模式下,chunks 只放行被「将合并的新卡」引用的 chunk_id;
      unreviewed 模式保留旧行为(合并全部 draft chunks)。
  - chunks 去重: chunk_id 重复则跳过
  - cards 去重:
      1) chunk_id 已存在 → 跳过
      2) source_url + 概念句指纹相同 → 跳过
      3) (concept + core_claim 去空白后 hash) 已存在 → 跳过
  - 仅合并 include_in_tutor=true 的卡片
  - merge 后 batch status → approved,写 approval.json
  - 提示用户运行 generate_mentor_profiles.py --force 刷新画像

用法:
  python scripts/approve_mentor_ingest_batch.py --batch <batch_id> --decision approve
  python scripts/approve_mentor_ingest_batch.py --batch <batch_id> --decision approve --dry-run
  python scripts/approve_mentor_ingest_batch.py --batch <batch_id> --decision approve --allow-unreviewed
  python scripts/approve_mentor_ingest_batch.py --batch <batch_id> --decision reject  --note "ASR 太脏"
  python scripts/approve_mentor_ingest_batch.py --batch <batch_id> --decision archive --note "仅留素材"
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.mentor_ingest_service import (  # noqa: E402
    batch_dir, load_manifest, set_status, write_approval,
    write_ingest_report, backup_official_knowledge,
)
from services.mentor_anonymize_service import (  # noqa: E402
    build_anonymizer, anonymize_card, anonymize_chunk,
)

OFFICIAL_DIR = Path("data/mentor_knowledge")
OFFICIAL_CARDS = OFFICIAL_DIR / "cards.json"
OFFICIAL_CHUNKS = OFFICIAL_DIR / "chunks.json"
OFFICIAL_REJECTED = OFFICIAL_DIR / "rejected_cards.json"


def _load_json_list(path: Path) -> list:
    if not path.exists():
        return []
    try:
        v = json.loads(path.read_text(encoding="utf-8"))
        return v if isinstance(v, list) else []
    except Exception:
        return []


def _save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _card_fingerprint(card: dict) -> str:
    concept = (card.get("concept") or "").strip()
    claim = (card.get("core_claim") or "").strip()
    h = hashlib.sha1(f"{concept}|{claim}".encode("utf-8")).hexdigest()
    return h[:16]


def merge_to_official(batch_id: str, dry_run: bool, allow_unreviewed: bool = False) -> dict:
    bd = batch_dir(batch_id)
    draft_cards_path = bd / "draft_cards.json"
    approved_cards_path = bd / "approved_cards.json"
    draft_rejected_path = bd / "rejected_cards.json"
    draft_chunks_path = bd / "chunks.json"

    # ── P1 审核闸门:决定合并哪些卡 ──────────────────────
    # 默认只认 approved_cards.json(管理端逐条审核导出)。
    # approved_cards.json 不存在时默认拒绝;--allow-unreviewed 才回退 draft_cards 全量。
    reviewed_mode = approved_cards_path.exists() and not allow_unreviewed
    if reviewed_mode:
        cards_to_merge = _load_json_list(approved_cards_path)
        card_source = "approved_cards.json"
    else:
        if not allow_unreviewed:
            return {
                "ok": False,
                "error": (
                    "未找到 approved_cards.json:请先在管理端「卡片审核闸门」逐条审核并导出。"
                    "如确需跳过审核合并 draft 全量,请显式传 --allow-unreviewed。"
                ),
            }
        cards_to_merge = _load_json_list(draft_cards_path)
        card_source = "draft_cards.json (UNREVIEWED)"

    draft_rejected = _load_json_list(draft_rejected_path)
    draft_chunks = _load_json_list(draft_chunks_path)

    if not cards_to_merge:
        return {"ok": False, "error": f"待合并卡片数为 0(source={card_source}),禁止 approve"}

    # ── 身份脱敏:合并前对 draft cards/chunks 应用 identity_map ─────
    # identity_map 缺失时静默退化(等价于"未脱敏",由后续 anonymize 脚本补)。
    anonymizer = build_anonymizer()
    if anonymizer.identities:
        cards_to_merge = [anonymize_card(anonymizer, c) for c in cards_to_merge]
        draft_chunks = [anonymize_chunk(anonymizer, ch) for ch in draft_chunks]
        draft_rejected = [anonymize_card(anonymizer, c) for c in draft_rejected]

    official_cards = _load_json_list(OFFICIAL_CARDS)
    official_chunks = _load_json_list(OFFICIAL_CHUNKS)
    official_rejected = _load_json_list(OFFICIAL_REJECTED)

    existing_chunk_ids = {c.get("chunk_id") for c in official_chunks}
    existing_card_chunk_ids = {c.get("chunk_id") for c in official_cards}
    existing_url_concept = {
        f"{c.get('source_url','')}|{_card_fingerprint(c)}"
        for c in official_cards if c.get("source_url")
    }
    existing_fingerprints = {_card_fingerprint(c) for c in official_cards}

    # cards (仅 include_in_tutor=true)
    new_cards = []
    skip_card_chunk_dup = 0
    skip_card_url_dup = 0
    skip_card_fp_dup = 0
    skip_card_excluded = 0
    for c in cards_to_merge:
        if not c.get("include_in_tutor", True):
            skip_card_excluded += 1
            continue
        cid = c.get("chunk_id")
        if cid and cid in existing_card_chunk_ids:
            skip_card_chunk_dup += 1
            continue
        url = c.get("source_url", "")
        fp = _card_fingerprint(c)
        if url and f"{url}|{fp}" in existing_url_concept:
            skip_card_url_dup += 1
            continue
        if fp in existing_fingerprints:
            skip_card_fp_dup += 1
            continue
        new_cards.append(c)
        existing_fingerprints.add(fp)
        existing_card_chunk_ids.add(cid)

    # ── chunks 闸门 ──────────────────────────────────────
    # reviewed_mode:只放行被「将要合并的新卡」引用的 chunk,避免被拒内容
    #   通过 chunk 级检索泄漏进正式库。
    # unreviewed:保留旧行为,合并全部 draft chunks。
    if reviewed_mode:
        wanted_chunk_ids = {c.get("chunk_id") for c in new_cards if c.get("chunk_id")}
        candidate_chunks = [ch for ch in draft_chunks if ch.get("chunk_id") in wanted_chunk_ids]
    else:
        candidate_chunks = draft_chunks

    new_chunks = []
    skip_chunks_dup = 0
    for ch in candidate_chunks:
        if ch.get("chunk_id") in existing_chunk_ids:
            skip_chunks_dup += 1
            continue
        new_chunks.append(ch)

    if new_cards == [] and new_chunks == []:
        return {"ok": False, "error": f"去重后无新卡/新 chunk 可合并(source={card_source}),禁止 approve"}

    summary = {
        "card_source": card_source,
        "reviewed_mode": reviewed_mode,
        "draft_chunks": len(draft_chunks),
        "cards_to_merge": len(cards_to_merge),
        "draft_rejected": len(draft_rejected),
        "merged_chunks": len(new_chunks),
        "merged_cards": len(new_cards),
        "skip_chunks_duplicate": skip_chunks_dup,
        "skip_cards_excluded": skip_card_excluded,
        "skip_cards_chunk_dup": skip_card_chunk_dup,
        "skip_cards_url_dup": skip_card_url_dup,
        "skip_cards_fingerprint_dup": skip_card_fp_dup,
    }

    if dry_run:
        summary["dry_run"] = True
        return {"ok": True, **summary}

    # 备份 → 写入
    backup = backup_official_knowledge()
    summary["backup"] = backup

    if new_chunks:
        _save_json(OFFICIAL_CHUNKS, official_chunks + new_chunks)
    if new_cards:
        _save_json(OFFICIAL_CARDS, official_cards + new_cards)
    # rejected 也合并(可选,不参与 tutor 检索,只保留审计)
    if draft_rejected:
        _save_json(OFFICIAL_REJECTED, official_rejected + draft_rejected)

    return {"ok": True, **summary}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="P4C 批次审核合并")
    ap.add_argument("--batch", required=True)
    ap.add_argument("--decision", required=True,
                    choices=["approve", "reject", "archive"])
    ap.add_argument("--note", default="")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--allow-unreviewed", action="store_true",
                    help="跳过 P1 审核闸门,直接合并 draft_cards 全量(应急用,默认禁止)")
    args = ap.parse_args(argv)

    m = load_manifest(args.batch)
    if not m:
        print(f"[ERROR] batch 不存在: {args.batch}")
        return 2

    if args.decision == "reject":
        if not args.dry_run:
            write_approval(args.batch, "rejected", args.note)
            set_status(args.batch, "rejected", note=args.note)
        print(f"[OK] batch {args.batch} 已驳回")
        return 0

    if args.decision == "archive":
        if not args.dry_run:
            write_approval(args.batch, "archive_only", args.note)
            set_status(args.batch, "archived", note=args.note)
        print(f"[OK] batch {args.batch} 仅归档,未合并到正式库")
        return 0

    # approve
    result = merge_to_official(args.batch, args.dry_run, allow_unreviewed=args.allow_unreviewed)
    if not result.get("ok"):
        print(f"[ERROR] {result.get('error')}")
        return 1

    if args.dry_run:
        print(f"[DRY-RUN] 合并预览: {result}")
        return 0

    write_approval(args.batch, "approved", args.note)
    set_status(args.batch, "approved", note=f"merged cards={result['merged_cards']} chunks={result['merged_chunks']}")
    write_ingest_report(args.batch, {
        "phase": "approve",
        "decision": "approve",
        "decided_at": datetime.now().isoformat(timespec="seconds"),
        **result,
    })
    print(f"[OK] batch {args.batch} 已合并到正式库")
    print(f"[OK] {result}")
    print("[NEXT] 建议运行: python scripts/generate_mentor_profiles.py --force")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
