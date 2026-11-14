"""
scripts/merge_developer_selected_samples.py
将多个 DeepSeek 筛选 run 的结果合并成最终候选数据集。

用法：
  # 合并所有 run
  python scripts/merge_developer_selected_samples.py

  # 指定白名单
  python scripts/merge_developer_selected_samples.py --runs batch01_small_v4 batch02_small_extra

  # 排除指定 run
  python scripts/merge_developer_selected_samples.py --exclude-runs batch01_small

  # 自定义输出目录
  python scripts/merge_developer_selected_samples.py --output-dir data/developer/processed/final_dataset

  # 预览（不写文件）
  python scripts/merge_developer_selected_samples.py --dry-run
"""

import sys
import re
import json
import hashlib
import argparse
from datetime import datetime
from pathlib import Path

ROOT     = Path(__file__).resolve().parent.parent
RUNS_DIR = ROOT / "data" / "developer" / "processed" / "selected_dialogues" / "runs"
OUT_DIR  = ROOT / "data" / "developer" / "processed" / "final_dataset"

# ─── 去重 helpers ─────────────────────────────────────────────

def _text_hash(s: str) -> str:
    return hashlib.md5(s.encode("utf-8")).hexdigest()


def _dialogue_hash(dialogue: list) -> str:
    raw = "".join(msg.get("content", "") for msg in dialogue)
    return _text_hash(raw)


def _jsonl_key(obj: dict) -> str:
    """优先用 case_id；缺失时用 source_file+scene+dialogue_hash"""
    if obj.get("case_id", "").strip():
        return obj["case_id"]
    h = _dialogue_hash(obj.get("dialogue", []))
    return f'{obj.get("source_file","")}__{obj.get("scene","")}__{h}'


# ─── TXT 样本解析 ─────────────────────────────────────────────
# 目前 core / negative txt 都以 【样本ID】xxx 或 【负面样本ID】xxx 开头

_ID_PAT = re.compile(r"【(?:样本|负面样本)ID】(.+)")

def _split_txt_samples(text: str) -> list[dict]:
    """
    将 core_style_samples.txt / negative_samples.txt 按分隔线切分成样本列表。
    每个样本是 {"id": str|None, "body": str}
    """
    sep = "─" * 60
    chunks = text.split(sep)
    samples = []
    for chunk in chunks:
        body = chunk.strip()
        if not body:
            continue
        m = _ID_PAT.search(body)
        sample_id = m.group(1).strip() if m else None
        samples.append({"id": sample_id, "body": body})
    return samples


def _txt_key(sample: dict) -> str:
    if sample["id"]:
        return sample["id"]
    return _text_hash(sample["body"])


# ─── 合并 JSONL ───────────────────────────────────────────────

def merge_jsonl_files(paths: list[Path]) -> tuple[list, int]:
    """
    读取多个 .jsonl 文件，去重，返回 (去重后列表, 去重移除数量)
    """
    seen: set = set()
    results: list = []
    removed = 0
    for p in paths:
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            key = _jsonl_key(obj)
            if key in seen:
                removed += 1
                continue
            seen.add(key)
            results.append(obj)
    return results, removed


# ─── 合并 TXT ────────────────────────────────────────────────

def merge_txt_files(paths: list[Path], run_ids: list[str],
                    add_run_header: bool = True) -> tuple[str, int]:
    """
    读取多个 .txt 文件，去重，拼接，可在每个 run 前插入来源标记。
    返回 (合并后文本, 去重移除数量)
    """
    sep = "─" * 60
    seen: set = set()
    parts: list[str] = []
    removed = 0

    for p, run_id in zip(paths, run_ids):
        if not p.exists():
            continue
        raw = p.read_text(encoding="utf-8")
        samples = _split_txt_samples(raw)
        run_parts: list[str] = []
        for s in samples:
            key = _txt_key(s)
            if key in seen:
                removed += 1
                continue
            seen.add(key)
            run_parts.append(s["body"])

        if not run_parts:
            continue
        if add_run_header:
            parts.append(f"{'═' * 60}\n【来源run】{run_id}\n{'═' * 60}\n")
        for body in run_parts:
            parts.append(body)
            parts.append(f"\n{sep}\n\n")

    return "".join(parts), removed


# ─── 主流程 ───────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="合并多批次 DeepSeek 筛选结果")
    parser.add_argument("--runs",         nargs="*", default=[], metavar="RUN_ID",
                        help="指定要合并的 run_id 白名单（默认全部）")
    parser.add_argument("--exclude-runs", nargs="*", default=[], metavar="RUN_ID",
                        help="排除指定 run_id")
    parser.add_argument("--runs-dir",     type=str, default="",
                        help="自定义 runs 根目录")
    parser.add_argument("--output-dir",   type=str, default="",
                        help="自定义输出目录")
    parser.add_argument("--dry-run",      action="store_true",
                        help="只预览，不写文件")
    args = parser.parse_args()

    runs_dir = Path(args.runs_dir) if args.runs_dir else RUNS_DIR
    out_dir  = Path(args.output_dir) if args.output_dir else OUT_DIR

    if not runs_dir.exists():
        print(f"\n[错误] runs 目录不存在：{runs_dir}\n")
        sys.exit(1)

    # ── 收集有效 run 目录 ──────────────────────────────────────
    all_run_dirs = sorted(
        [d for d in runs_dir.iterdir() if d.is_dir() and d.name != "latest"],
        key=lambda d: d.name
    )

    if args.runs:
        # 白名单模式
        whiteset = set(args.runs)
        selected = [d for d in all_run_dirs if d.name in whiteset]
        missing  = whiteset - {d.name for d in selected}
        if missing:
            print(f"[警告] 以下 run_id 未找到目录，已跳过：{', '.join(sorted(missing))}")
    else:
        selected = all_run_dirs

    if args.exclude_runs:
        excset   = set(args.exclude_runs)
        selected = [d for d in selected if d.name not in excset]

    if not selected:
        print("\n[提示] 没有找到任何有效 run 目录，退出\n")
        sys.exit(0)

    run_ids = [d.name for d in selected]

    print(f"\n{'='*64}")
    print(f"  开发者素材合并{'  [DRY-RUN 预览]' if args.dry_run else ''}")
    print(f"  runs 根目录：{runs_dir}")
    print(f"  将合并 {len(selected)} 个 run：")
    for d in selected:
        print(f"    {d.name}")
    if not args.dry_run:
        print(f"  输出目录：{out_dir}")
    print(f"{'='*64}\n")

    # ── 统计 per-run 概况 ──────────────────────────────────────
    by_run: dict = {}
    for run_id, run_dir in zip(run_ids, selected):
        log_path = run_dir / "selection_log.json"
        if log_path.exists():
            try:
                log = json.loads(log_path.read_text(encoding="utf-8"))
                s   = log.get("summary", {})
                by_run[run_id] = {
                    "core":       s.get("core_style_samples", 0),
                    "advanced":   s.get("advanced_style_cases", 0),
                    "restricted": s.get("restricted_cases", 0),
                    "negative":   s.get("negative_samples", 0),
                    "case_cards": s.get("total_case_cards", 0),
                }
            except Exception:
                by_run[run_id] = {"core": "?", "advanced": "?",
                                   "restricted": "?", "negative": "?", "case_cards": "?"}
        else:
            by_run[run_id] = {"core": "?", "advanced": "?",
                               "restricted": "?", "negative": "?", "case_cards": "?"}

        r = by_run[run_id]
        print(f"  [{run_id}]  "
              f"core={r['core']}  adv={r['advanced']}  "
              f"rest={r['restricted']}  neg={r['negative']}  "
              f"cards={r['case_cards']}")

    print()

    if args.dry_run:
        print("DRY-RUN 完成，未写入任何文件\n")
        sys.exit(0)

    # ── 准备输出目录 ───────────────────────────────────────────
    if out_dir.exists():
        print(f"[提示] 输出目录已存在，将覆盖：{out_dir}\n")
    out_dir.mkdir(parents=True, exist_ok=True)

    dedup = {"core_removed": 0, "advanced_removed": 0,
             "restricted_removed": 0, "case_cards_removed": 0}

    # ── 1. 合并 core_style_samples.txt ────────────────────────
    print("合并 core_style_samples.txt ...", end=" ", flush=True)
    core_paths  = [d / "core_style_samples.txt"      for d in selected]
    core_text, core_rm = merge_txt_files(core_paths, run_ids)
    (out_dir / "all_core_style_candidates.txt").write_text(core_text, encoding="utf-8")
    dedup["core_removed"] = core_rm
    core_count = len(_split_txt_samples(core_text))
    print(f"完成  候选：{core_count}  去重移除：{core_rm}")

    # ── 2. 合并 advanced_style_cases.jsonl ────────────────────
    print("合并 advanced_style_cases.jsonl ...", end=" ", flush=True)
    adv_paths   = [d / "advanced_style_cases.jsonl"  for d in selected]
    adv_list, adv_rm = merge_jsonl_files(adv_paths)
    with (out_dir / "all_advanced_style_cases.jsonl").open("w", encoding="utf-8") as f:
        for obj in adv_list:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")
    dedup["advanced_removed"] = adv_rm
    print(f"完成  条目：{len(adv_list)}  去重移除：{adv_rm}")

    # ── 3. 合并 restricted_cases.jsonl ────────────────────────
    print("合并 restricted_cases.jsonl ...", end=" ", flush=True)
    rest_paths  = [d / "restricted_cases.jsonl"      for d in selected]
    rest_list, rest_rm = merge_jsonl_files(rest_paths)
    with (out_dir / "all_restricted_cases.jsonl").open("w", encoding="utf-8") as f:
        for obj in rest_list:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")
    dedup["restricted_removed"] = rest_rm
    print(f"完成  条目：{len(rest_list)}  去重移除：{rest_rm}")

    # ── 4. 合并 negative_samples.txt ──────────────────────────
    print("合并 negative_samples.txt ...", end=" ", flush=True)
    neg_paths   = [d / "negative_samples.txt"        for d in selected]
    neg_text, neg_rm = merge_txt_files(neg_paths, run_ids)
    (out_dir / "all_negative_samples.txt").write_text(neg_text, encoding="utf-8")
    neg_count = len(_split_txt_samples(neg_text))
    print(f"完成  条目：{neg_count}  去重移除：{neg_rm}")

    # ── 5. 合并 case_cards.jsonl ──────────────────────────────
    print("合并 case_cards.jsonl ...", end=" ", flush=True)
    card_paths  = [d / "case_cards.jsonl"            for d in selected]
    card_list, card_rm = merge_jsonl_files(card_paths)
    with (out_dir / "all_case_cards.jsonl").open("w", encoding="utf-8") as f:
        for obj in card_list:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")
    dedup["case_cards_removed"] = card_rm
    print(f"完成  条目：{len(card_list)}  去重移除：{card_rm}")

    # ── 6. summary.json ───────────────────────────────────────
    summary = {
        "merged_at":  datetime.now().isoformat(),
        "runs_used":  run_ids,
        "counts": {
            "core_candidates":  core_count,
            "advanced_cases":   len(adv_list),
            "restricted_cases": len(rest_list),
            "negative_samples": neg_count,
            "case_cards":       len(card_list),
        },
        "by_run": by_run,
        "dedup":  dedup,
    }
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    # ── 汇总输出 ──────────────────────────────────────────────
    print(f"\n{'─'*64}")
    print(f"  合并完成")
    print(f"  core 候选：{core_count} 个  →  all_core_style_candidates.txt")
    print(f"  advanced：{len(adv_list)} 条  →  all_advanced_style_cases.jsonl")
    print(f"  restricted：{len(rest_list)} 条  →  all_restricted_cases.jsonl")
    print(f"  negative：{neg_count} 条  →  all_negative_samples.txt")
    print(f"  case_cards：{len(card_list)} 条  →  all_case_cards.jsonl")
    print(f"  去重移除：core={dedup['core_removed']}  adv={dedup['advanced_removed']}  "
          f"rest={dedup['restricted_removed']}  cards={dedup['case_cards_removed']}")
    print(f"  输出目录：{out_dir}")
    print(f"{'─'*64}\n")


if __name__ == "__main__":
    main()
