"""
scripts/curate_final_dna_samples.py
从合并后的候选池中精选最终核心 DNA 样本集。

用法：
  python scripts/curate_final_dna_samples.py --dry-run
  python scripts/curate_final_dna_samples.py
  python scripts/curate_final_dna_samples.py --target-core 150 --max-per-contact 25
"""

import sys
import re
import json
import hashlib
import random
import argparse
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT      = Path(__file__).resolve().parent.parent
IN_DIR    = ROOT / "data" / "developer" / "processed" / "final_dataset"
OUT_DIR   = IN_DIR / "curated"

# ─── 低信息密度过滤 ───────────────────────────────────────────
_LOW_INFO_PATTERNS = [
    re.compile(r'^[好嗯哦啊哈呵嘿哼唔哇噢哎]{1,3}$'),
    re.compile(r'^[。！？…~～]+$'),
    re.compile(r'^\[图片\]$'),
    re.compile(r'^\[表情\]$'),
    re.compile(r'^\[语音\]$'),
    re.compile(r'^\[视频\]$'),
    re.compile(r'^没呢?$'),
    re.compile(r'^在$'),
    re.compile(r'^嗯嗯?$'),
]

# 高风险关键词 → 不进 core（进 signature 或 rejected）
_CORE_EXCLUDE_KW = [
    "睡", "摸", "宝宝", "酒店", "喝酒", "醉", "诱惑", "坏学生", "流氓",
    "性", "前任", "吊着", "多线", "拧巴", "端着", "放不下", "握不住",
    "吸渣", "耗光", "没珍惜", "亲亲", "抱抱",
]

# 高辨识度关键词 → 优先进 signature
_SIGNATURE_KW = [
    "喜欢", "心动", "攻略", "姐姐", "破例", "嫁你", "娶我", "入赘",
    "腰", "身材", "细细", "理想型", "想你", "忙完找我", "有空来找我",
    "很美式", "框架", "反问", "锋利",
]


# ─── helpers ─────────────────────────────────────────────────

def _text_hash(s: str) -> str:
    return hashlib.md5(s.encode("utf-8")).hexdigest()[:12]


def _dialogue_text(item: dict) -> str:
    return " ".join(m.get("content", "") for m in item.get("dialogue", []))


def _dialogue_hash(item: dict) -> str:
    return _text_hash(_dialogue_text(item))


def _normalize_tags(tags: list) -> str:
    return "|".join(sorted(str(t).strip() for t in tags if t))


def _normalize_mechanism(s: str) -> str:
    return re.sub(r'\s+', '', s or "").lower()[:40]


def _is_low_info(item: dict) -> bool:
    """对话内容是否低信息密度"""
    msgs = item.get("dialogue", [])
    if not msgs:
        return True
    user_msgs = [m for m in msgs if m.get("role") == "user"]
    if not user_msgs:
        return True
    # 如果所有我方消息都是低信息
    for m in user_msgs:
        content = m.get("content", "").strip()
        for pat in _LOW_INFO_PATTERNS:
            if pat.match(content):
                break
        else:
            return False  # 至少一条有信息量
    return True


def _has_core_exclude_kw(item: dict) -> bool:
    text = _dialogue_text(item)
    return any(kw in text for kw in _CORE_EXCLUDE_KW)


def _has_signature_kw(item: dict) -> bool:
    text = _dialogue_text(item)
    return any(kw in text for kw in _SIGNATURE_KW)


def _score_core_candidate(item: dict) -> float:
    """给 core 候选打分，分越高越优先保留"""
    score = float(item.get("quality_score", 0))

    # 风险等级加权
    risk = item.get("risk_level", "low")
    if risk == "low":
        score += 1.5
    elif risk == "medium":
        score += 0.5

    # style_mechanism 清晰加分
    mech = (item.get("style_mechanism") or "").strip()
    if len(mech) > 10:
        score += 1.0

    # dialogue 短但有内容加分（信息密度）
    msgs = item.get("dialogue", [])
    total_chars = sum(len(m.get("content", "")) for m in msgs)
    if 0 < total_chars <= 80:
        score += 0.8
    elif total_chars <= 150:
        score += 0.4

    # tags 丰富加分
    tags = item.get("tags", [])
    if len(tags) >= 3:
        score += 0.5

    # 低信息密度扣分
    if _is_low_info(item):
        score -= 3.0

    return score


def _score_signature_candidate(item: dict) -> float:
    score = float(item.get("quality_score", 0))
    if _has_signature_kw(item):
        score += 1.0
    mech = (item.get("style_mechanism") or "").strip()
    if len(mech) > 10:
        score += 0.8
    if _is_low_info(item):
        score -= 3.0
    return score


def _build_restricted_mechanism(item: dict) -> dict:
    """从 restricted case 提炼机制对象"""
    tags = item.get("tags", [])
    scene = item.get("scene", "")
    why = item.get("why_good", "")
    mech_raw = item.get("style_mechanism", "")
    usage = item.get("usage_conditions", [])
    forbidden = item.get("forbidden_contexts", [])
    risk_text = item.get("risk", "")

    mechanism_name = mech_raw[:20] if mech_raw else (scene or "未命名机制")

    return {
        "mechanism":          mechanism_name,
        "source_case_id":     item.get("case_id", ""),
        "contact_name":       item.get("contact_name", ""),
        "raw_style":          why[:100] if why else "",
        "why_powerful":       why,
        "style_mechanism":    mech_raw,
        "tags":               tags,
        "scene":              scene,
        "stage":              item.get("stage", ""),
        "usage_conditions":   usage,
        "forbidden_contexts": forbidden,
        "risk":               risk_text,
        "safe_translation":   "",   # 留给后续人工填写
        "quality_score":      item.get("quality_score", 0),
    }


def _build_negative_rule(item: dict) -> dict:
    return {
        "rule_id":        item.get("case_id", ""),
        "contact_name":   item.get("contact_name", ""),
        "scene":          item.get("scene", ""),
        "why_bad":        item.get("why_good", ""),
        "tags":           item.get("tags", []),
        "dialogue_hash":  _dialogue_hash(item),
        "lesson":         item.get("why_good", ""),
        "avoid_pattern":  item.get("style_mechanism", ""),
    }


def _core_to_txt_block(item: dict) -> str:
    sep = "─" * 60
    dialogue_lines = []
    contact = item.get("contact_name", "对方")
    for m in item.get("dialogue", []):
        label = "我" if m.get("role") == "user" else contact
        dialogue_lines.append(f"{label}：{m.get('content', '')}")
    tags_str = "\n".join(f"- {t}" for t in item.get("tags", [])) or "- 无"
    return (
        f"【样本ID】{item.get('case_id', '')}\n"
        f"【联系人】{contact}\n"
        f"【场景】{item.get('scene', '')}\n"
        f"【关系阶段】{item.get('stage', '')}\n"
        f"【质量分】{item.get('quality_score', 0)}/10  "
        f"【风险等级】{item.get('risk_level', '')}\n\n"
        f"【原始对话】\n{chr(10).join(dialogue_lines)}\n\n"
        f"【为什么值得学习】\n{item.get('why_good', '')}\n\n"
        f"【风格机制】\n{item.get('style_mechanism', '')}\n\n"
        f"【可学习点】\n{tags_str}\n\n"
        f"【风险】\n{item.get('risk') or '无'}\n\n"
        f"{sep}\n\n"
    )


# ─── 主流程 ───────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="精选最终核心 DNA 样本集")
    parser.add_argument("--input-dir",        type=str, default="")
    parser.add_argument("--output-dir",       type=str, default="")
    parser.add_argument("--target-core",      type=int, default=150)
    parser.add_argument("--target-signature", type=int, default=60)
    parser.add_argument("--target-advanced",  type=int, default=60)
    parser.add_argument("--target-restricted",type=int, default=40)
    parser.add_argument("--target-negative",  type=int, default=20)
    parser.add_argument("--max-per-contact",  type=int, default=25)
    parser.add_argument("--dry-run",          action="store_true")
    parser.add_argument("--use-deepseek",     action="store_true",
                        help="（当前版本暂未启用 DeepSeek 二次精选）")
    parser.add_argument("--seed",             type=int, default=42)
    args = parser.parse_args()

    if args.use_deepseek:
        print("[提示] 当前版本暂未启用 DeepSeek 二次精选，将使用本地规则运行\n")

    random.seed(args.seed)

    in_dir  = Path(args.input_dir)  if args.input_dir  else IN_DIR
    out_dir = Path(args.output_dir) if args.output_dir else OUT_DIR

    cards_path = in_dir / "all_case_cards.jsonl"
    if not cards_path.exists():
        print(f"\n[错误] 找不到输入文件：{cards_path}\n")
        sys.exit(1)

    # ── 读取全部 case cards ────────────────────────────────────
    all_cards: list[dict] = []
    for line in cards_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            all_cards.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    # 按 case_type 分桶
    buckets: dict[str, list] = defaultdict(list)
    for card in all_cards:
        ct = card.get("case_type", "unknown")
        buckets[ct].append(card)

    core_pool     = buckets.get("core_style", [])
    advanced_pool = buckets.get("advanced_style", [])
    restricted_pool = buckets.get("restricted", [])
    negative_pool = buckets.get("negative", [])

    print(f"\n{'='*64}")
    print(f"  最终 DNA 样本精选{'  [DRY-RUN]' if args.dry_run else ''}")
    print(f"  输入：{cards_path}")
    print(f"  总 case cards：{len(all_cards)}")
    print(f"    core_style：{len(core_pool)}")
    print(f"    advanced_style：{len(advanced_pool)}")
    print(f"    restricted：{len(restricted_pool)}")
    print(f"    negative：{len(negative_pool)}")
    print(f"{'='*64}\n")

    # ── 联系人分布统计 ─────────────────────────────────────────
    contact_dist: dict[str, int] = defaultdict(int)
    for c in core_pool:
        contact_dist[c.get("contact_name", "unknown")] += 1

    print("  core 候选联系人分布：")
    for name, cnt in sorted(contact_dist.items(), key=lambda x: -x[1]):
        flag = "  ⚠ 超大" if cnt > args.max_per_contact * 2 else ""
        print(f"    {name}: {cnt}{flag}")
    print()

    # ── 精选 core ─────────────────────────────────────────────
    # Step 1: 过滤明显不合格
    core_eligible   = []
    core_rejected   = []
    seen_dhash: set = set()
    seen_mech:  set = set()

    for item in core_pool:
        reasons = []

        if _is_low_info(item):
            reasons.append("低信息密度")
        if _has_core_exclude_kw(item):
            reasons.append("含高风险关键词")

        dhash = _dialogue_hash(item)
        if dhash in seen_dhash:
            reasons.append("对话内容重复")
        else:
            seen_dhash.add(dhash)

        mkey = _normalize_mechanism(item.get("style_mechanism", ""))
        if mkey and mkey in seen_mech:
            reasons.append("风格机制重复")
        elif mkey:
            seen_mech.add(mkey)

        if reasons:
            item["_reject_reasons"] = reasons
            core_rejected.append(item)
        else:
            core_eligible.append(item)

    # Step 2: 打分排序
    core_eligible.sort(key=_score_core_candidate, reverse=True)

    # Step 3: 按联系人配额选取
    contact_quota: dict[str, int] = defaultdict(int)
    final_core:    list[dict]     = []
    overflow_core: list[dict]     = []

    for item in core_eligible:
        contact = item.get("contact_name", "unknown")
        if contact_quota[contact] < args.max_per_contact:
            final_core.append(item)
            contact_quota[contact] += 1
        else:
            overflow_core.append(item)

    # 如果还没到 target，从 overflow 补充（放宽配额）
    if len(final_core) < args.target_core:
        for item in overflow_core:
            if len(final_core) >= args.target_core:
                break
            final_core.append(item)

    # 截断到 target
    final_core = final_core[:args.target_core]

    # 剩余进 rejected
    selected_ids = {id(x) for x in final_core}
    for item in core_eligible:
        if id(item) not in selected_ids:
            item["_reject_reasons"] = item.get("_reject_reasons", []) + ["超出配额或数量上限"]
            core_rejected.append(item)

    # ── 精选 signature（从 core_eligible 中高辨识度 + 被排除的高分 core）──
    sig_pool: list[dict] = []
    # 从 core_eligible 中找有 signature 关键词但被配额挤出的
    for item in core_eligible:
        if id(item) not in selected_ids and _has_signature_kw(item):
            sig_pool.append(item)
    # 也从 advanced_pool 中找高辨识度
    for item in advanced_pool:
        if _has_signature_kw(item) and not _is_low_info(item):
            sig_pool.append(item)

    sig_pool.sort(key=_score_signature_candidate, reverse=True)

    # 去重（不与 final_core 重复）
    core_dhashes = {_dialogue_hash(x) for x in final_core}
    final_sig: list[dict] = []
    seen_sig_dhash: set = set()
    for item in sig_pool:
        dh = _dialogue_hash(item)
        if dh in core_dhashes or dh in seen_sig_dhash:
            continue
        seen_sig_dhash.add(dh)
        final_sig.append(item)
        if len(final_sig) >= args.target_signature:
            break

    # ── 精选 advanced ─────────────────────────────────────────
    adv_eligible = [x for x in advanced_pool
                    if not _is_low_info(x)
                    and _dialogue_hash(x) not in core_dhashes
                    and _dialogue_hash(x) not in seen_sig_dhash]
    adv_eligible.sort(key=lambda x: float(x.get("quality_score", 0)), reverse=True)

    # 联系人均衡
    adv_contact_quota: dict[str, int] = defaultdict(int)
    final_adv: list[dict] = []
    for item in adv_eligible:
        contact = item.get("contact_name", "unknown")
        if adv_contact_quota[contact] < max(5, args.max_per_contact // 3):
            final_adv.append(item)
            adv_contact_quota[contact] += 1
        if len(final_adv) >= args.target_advanced:
            break

    # ── 精选 restricted mechanisms ────────────────────────────
    rest_eligible = [x for x in restricted_pool if not _is_low_info(x)]
    rest_eligible.sort(key=lambda x: float(x.get("quality_score", 0)), reverse=True)

    # 按 mechanism 去重
    seen_rest_mech: set = set()
    final_rest_mechs: list[dict] = []
    for item in rest_eligible:
        mkey = _normalize_mechanism(item.get("style_mechanism", ""))
        if mkey and mkey in seen_rest_mech:
            continue
        if mkey:
            seen_rest_mech.add(mkey)
        final_rest_mechs.append(_build_restricted_mechanism(item))
        if len(final_rest_mechs) >= args.target_restricted:
            break

    # ── 精选 negative rules ───────────────────────────────────
    neg_eligible = list(negative_pool)
    # 也从 restricted 中补充典型负面
    for item in restricted_pool:
        why = item.get("why_good", "")
        if any(kw in why for kw in ["低姿态", "追问", "催", "讨好", "道歉", "施压"]):
            neg_eligible.append(item)

    neg_eligible.sort(key=lambda x: float(x.get("quality_score", 0)), reverse=True)
    seen_neg_dhash: set = set()
    final_neg_rules: list[dict] = []
    for item in neg_eligible:
        dh = _dialogue_hash(item)
        if dh in seen_neg_dhash:
            continue
        seen_neg_dhash.add(dh)
        final_neg_rules.append(_build_negative_rule(item))
        if len(final_neg_rules) >= args.target_negative:
            break

    # ── 统计 ──────────────────────────────────────────────────
    by_contact: dict[str, dict] = defaultdict(lambda: defaultdict(int))
    for item in final_core:
        by_contact[item.get("contact_name", "unknown")]["core"] += 1
    for item in final_sig:
        by_contact[item.get("contact_name", "unknown")]["signature"] += 1
    for item in final_adv:
        by_contact[item.get("contact_name", "unknown")]["advanced"] += 1

    by_tag: dict[str, int] = defaultdict(int)
    for item in final_core:
        for t in item.get("tags", []):
            by_tag[str(t)] += 1

    print(f"  精选结果预览：")
    print(f"    final_core_dna_samples：{len(final_core)}  (目标 {args.target_core})")
    print(f"    final_signature_style_cases：{len(final_sig)}  (目标 {args.target_signature})")
    print(f"    final_advanced_style_cases：{len(final_adv)}  (目标 {args.target_advanced})")
    print(f"    final_restricted_mechanisms：{len(final_rest_mechs)}  (目标 {args.target_restricted})")
    print(f"    final_negative_rules：{len(final_neg_rules)}  (目标 {args.target_negative})")
    print(f"    rejected_core_candidates：{len(core_rejected)}")
    print()
    print(f"  core 联系人分布（精选后）：")
    for name, counts in sorted(by_contact.items(), key=lambda x: -x[1].get("core", 0)):
        c = counts.get("core", 0)
        s = counts.get("signature", 0)
        a = counts.get("advanced", 0)
        print(f"    {name}: core={c}  sig={s}  adv={a}")
    print()

    if args.dry_run:
        print("DRY-RUN 完成，未写入任何文件\n")
        sys.exit(0)

    # ── 写文件 ────────────────────────────────────────────────
    if out_dir.exists():
        print(f"[提示] 输出目录已存在，将覆盖：{out_dir}\n")
    out_dir.mkdir(parents=True, exist_ok=True)

    def write_jsonl(path: Path, items: list):
        with path.open("w", encoding="utf-8") as f:
            for obj in items:
                f.write(json.dumps(obj, ensure_ascii=False) + "\n")

    # final_core_dna_samples.jsonl
    write_jsonl(out_dir / "final_core_dna_samples.jsonl", final_core)

    # final_core_dna_samples.txt
    txt_parts = [_core_to_txt_block(item) for item in final_core]
    (out_dir / "final_core_dna_samples.txt").write_text(
        "".join(txt_parts), encoding="utf-8")

    # final_signature_style_cases.jsonl
    write_jsonl(out_dir / "final_signature_style_cases.jsonl", final_sig)

    # final_advanced_style_cases.jsonl
    write_jsonl(out_dir / "final_advanced_style_cases.jsonl", final_adv)

    # final_restricted_mechanisms.jsonl
    write_jsonl(out_dir / "final_restricted_mechanisms.jsonl", final_rest_mechs)

    # final_negative_rules.jsonl
    write_jsonl(out_dir / "final_negative_rules.jsonl", final_neg_rules)

    # rejected_core_candidates.jsonl
    write_jsonl(out_dir / "rejected_core_candidates.jsonl", core_rejected)

    # curation_summary.json
    summary = {
        "created_at": datetime.now().isoformat(),
        "seed": args.seed,
        "params": {
            "target_core":       args.target_core,
            "target_signature":  args.target_signature,
            "target_advanced":   args.target_advanced,
            "target_restricted": args.target_restricted,
            "target_negative":   args.target_negative,
            "max_per_contact":   args.max_per_contact,
        },
        "input_counts": {
            "core_candidates":  len(core_pool),
            "advanced_cases":   len(advanced_pool),
            "restricted_cases": len(restricted_pool),
            "negative_samples": len(negative_pool),
            "case_cards":       len(all_cards),
        },
        "output_counts": {
            "final_core_dna_samples":      len(final_core),
            "final_signature_style_cases": len(final_sig),
            "final_advanced_style_cases":  len(final_adv),
            "final_restricted_mechanisms": len(final_rest_mechs),
            "final_negative_rules":        len(final_neg_rules),
            "rejected_core_candidates":    len(core_rejected),
        },
        "by_contact": {k: dict(v) for k, v in by_contact.items()},
        "by_tag":     dict(sorted(by_tag.items(), key=lambda x: -x[1])[:30]),
        "notes": [
            f"core 过滤：低信息密度/高风险关键词/重复对话/重复机制 共移除 {len(core_rejected)} 条",
            f"联系人配额：每人最多 {args.max_per_contact} 条进入 core",
            "restricted 只提炼机制，不保留原句",
            "当前版本使用本地规则，未启用 DeepSeek 二次精选",
        ],
    }
    (out_dir / "curation_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"{'─'*64}")
    print(f"  精选完成")
    print(f"  final_core_dna_samples：{len(final_core)} 条")
    print(f"  final_signature_style_cases：{len(final_sig)} 条")
    print(f"  final_advanced_style_cases：{len(final_adv)} 条")
    print(f"  final_restricted_mechanisms：{len(final_rest_mechs)} 条")
    print(f"  final_negative_rules：{len(final_neg_rules)} 条")
    print(f"  rejected_core_candidates：{len(core_rejected)} 条")
    print(f"  输出目录：{out_dir}")
    print(f"{'─'*64}\n")


if __name__ == "__main__":
    main()
