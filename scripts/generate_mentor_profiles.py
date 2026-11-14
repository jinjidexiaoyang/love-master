"""
P4B：导师画像生成脚本（离线一次性，DeepSeek）
- 从 cards.json 聚合 mentor / course 视角
- 每视角做"代表性采样"（高分 / 高频 tags & concept / 多 source_course / 多 card_type / 含少量 medium-high safety）
- 一次 LLM 调用产出固定 schema 画像
- 写入 data/mentor_knowledge/mentor_profiles.json + 同名 _report.json

不修改：cards.json / chunks.json / 核心 Agent / DNA / database。
不在脚本内默认运行：需要用户主动 `python scripts/generate_mentor_profiles.py` 触发。

CLI:
  --view <view_id>      仅生成单个视角（如 'mentor:<化名>' / 'course:<课程名>'）
  --dry-run             不写文件，仅打印第一个视角的输入采样和最终 JSON
  --force               忽略 source_hash 比对，强制重生成
  --min-cards N         视角最少卡片数，低于则记 insufficient_data（默认 3）
  --sample-per-view N   每视角最多采样卡片数（默认 40）
  --max-input-chars N   单视角输入字符上限（默认 8000，留余量给中文）
  --limit N             仅处理前 N 个视角（按 card_count 倒序，调试用）
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

# 让脚本既能 `python scripts/xxx.py`，也能 `python -m scripts.xxx`
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.deepseek_client import call_deepseek_json, DEEPSEEK_MODEL  # noqa: E402
from services.mentor_knowledge_service import (  # noqa: E402
    load_cards,
    list_available_mentor_views,
)

OUTPUT_PATH = Path("data/mentor_knowledge/mentor_profiles.json")
REPORT_PATH = Path("data/mentor_knowledge/mentor_profiles_report.json")


SYSTEM_PROMPT = """你是一名"导师思想蒸馏"分析师。任务是基于一位导师/课程视角的多张知识卡片,提炼该视角的"判断模型卡",尽可能保留每个导师/课程的原始判断模型、价值偏向、锋利观点、反常识判断、可执行方法论。

【这个画像的产品定位 — 必读】
- 这是"导师思想蒸馏",不是"普世温和建议",不是"心理咨询式总结",不是"安全合规摘要"。
- 多导师对比时,这份画像作为"视角先验"注入,目的是让模型还原该导师的真实判断方式,体现导师之间的差异。
- 如果所有导师最后都被你写成"温和情感咨询师"风格 — 就是失败。

【强制要求 — 防安全化、防大模型总结腔】
1. 不要把导师观点洗成"真诚沟通/尊重边界/慢慢来/理解对方/建立健康关系"。这些表达可以出现在 misuse_risks / risk_preference 里作为风险提示,但不能成为画像主体,不能覆盖原始立场。
2. 即使某些导师观点不完全符合普世温和叙事,也要保留其判断模型。例如:他为什么强调抽离 / 反对解释 / 强调框架 / 认为需求感会毁掉关系 / 主张快速筛选 / 认为某些场景不该继续情绪安抚 — 这些都要在 raw_stance / why_it_works / default_judgment_bias 中如实呈现。
3. 区分"原始立场"和"误用风险":先写 raw_stance(原始立场,保留锋利) + why_it_works(为什么有效) + fit_scenarios(适用场景),再写 misuse_risks(误用风险) + risk_preference。不要用风险提示把观点抹平。
4. 不要使用卡片里没有依据的学术或心理学包装词,例如"认知心理学/依恋理论/积极心理学/正念/原生家庭/依恋问题",除非卡片原文出现过。该约束适用于**所有字段**,包括 blind_spots / not_fit_scenarios / misuse_risks / risk_preference,不要在风险类字段偷偷塞这类词。优先复用 concept / tags / core_claim / actionable_moves 中出现过的卡片体系内表达。
5. 不要中性平衡文。如果卡片里这个导师就是激进/筛选/反讨好/进攻/博弈/现实主义 — 就如实标注,不要冲淡。
6. blind_spots / misuse_risks / risk_preference 都要具体化:写"在什么场景下、对什么用户、误用会出什么问题",不是"注意对方感受、谨慎平衡、把握分寸"。
7. 不要编造没有出现在卡片里的概念、术语、原话、人名。
8. 安全底线:不要直接输出违法、强迫、骚扰、无视明确拒绝的建议。如卡片含此类边缘内容,处理方式是 — raw_stance 保留判断逻辑、fit_scenarios 标注适用边界、misuse_risks 写明具体风险,而不是把观点本身改写成温和建议。
9. 证据不足的字段允许为空数组或空字符串,但不要用空话凑数。
10. **禁止照抄/套用本 prompt 中的示例句**。所有示例(包括 raw_stance / misuse_risks / why_it_works / risk_preference 字段定义里的引号内文字)仅用于说明"锋利度"和"格式",不得作为模板复用。如果你产出的 raw_stance 句式与示例高度相似(例如开头都是"关系不是靠解释维持的"/"需求感才是毁掉吸引力的根源"/"当对方降温,首要不是沟通,而是抽离让张力回归"),那就是失败。每个视角的 raw_stance 必须使用**该视角自己的卡片**中出现的语言、概念、立场。
11. **raw_stance 强制要求**:开头必须从该视角的 signature_concepts / core_topics / core_claim 中挑词切入,且整段至少包含 2 个该视角独有的概念。不同视角的 raw_stance 句式必须不同,不允许共用通用模板。例如(仅说明独有概念该长什么样,不是让你套用任何具体导师的词):某个心理投射视角会用"自恋/内心投射/能量现实/自我授权",某个抽离博弈视角会用"抽离/服从测试/框架博弈/忽略策略",某个进化心理视角会用"女本位/SMV/废测/α/β",某个向上社交视角会用"资源的本质/给予逻辑/通道意识"等等 — 你需要从**当前这个视角自己的卡片**里识别属于它自己的概念,不要跨视角借用。

【输出 JSON 字段定义】
{
  "style_archetype": "一句话风格类型,例:内核突破型 / 进攻推进型 / 现实筛选型 / 关系策略型 / 红药丸型 / 抽离博弈型,贴合卡片",
  "style": "1-2 句表达风格(语气/锋利度/常用切入角度)。直接、有判断。不准用'认知心理学/积极心理学'等学术包装,除非卡片原文有",
  "core_topics": ["该视角反复讨论的话题 3-8 个,贴近卡片用词"],
  "signature_concepts": ["该视角独有或高频的概念名 3-8 个,必须出自卡片 concept / tags / core_claim"],
  "typical_moves": ["该视角典型的可执行动作 3-6 条,基于 actionable_moves"],
  "raw_stance": "该导师/课程视角在该类问题上的原始立场,2-4 句。保留锋利度、保留反常识判断,这一字段就是导师思想的核心,禁止温和化改写。**写作约束**:开头必须从该视角自己的 signature_concepts / core_topics / core_claim 切入,整段至少包含 2 个该视角独有概念,不得套用其他视角的句式或本 prompt 示例的句式。**反例(禁止照抄/近似)**:'关系不是靠解释维持的,需求感才是毁掉吸引力的根源' / '当对方降温,首要不是沟通,而是抽离让张力回归' — 这些是格式说明,不是模板。你必须用该视角自己卡片里的概念另起句式。",
  "why_it_works": "这个方法/立场为什么可能有效。说明它抓住了什么人性、关系动力或互动机制。1-3 句,基于卡片 core_claim / common_mistakes 的反面。例:'人对掌控感和稀缺度天然敏感,持续解释会把不确定性单方面放在自己身上,反而强化对方的优势位。'",
  "default_judgment_bias": "该导师遇到关系/社交问题时的默认归因。具体,不要写成'综合分析'。例:'优先归因于用户在求认可/制造需求感' / '优先看窗口期与反馈推进' / '优先看投资比例与现实筛选'",
  "would_disagree_with": ["该导师会明确反对的 3-5 类常见建议或做法,锋利具体,基于卡片 common_mistakes 与反面表达。例:'长篇解释证明自己'/'把不回复归因于对方忙'/'用礼物或讨好维持关系'/'忽略 IOD 硬推升级'/'劝用户先去做心理建设再行动'。必须有依据,不要写成温和表达"],
  "edge_or_distinctive_angle": "一句话写出该导师最有辨识度、最不容易被普通 AI 写出来的观点。要具体、有味道、反常识。不要写成'帮助用户成长'这种空话",
  "strengths": ["该视角擅长解决的问题 2-5 条"],
  "blind_spots": ["该视角作为策略本身的局限,在什么场景下会失灵或代价过高 1-4 条。不是道德审查"],
  "fit_scenarios": ["最适合采用该视角的场景 2-5 条"],
  "not_fit_scenarios": ["不适合采用该视角的场景 1-4 条"],
  "misuse_risks": ["用户误用这套思路时会出什么问题 2-5 条。注意:这是风险说明,不要把导师观点改写成温和建议。例:'若用户缺少反馈识别能力,把抽离误用成单向冷战导致关系彻底冷掉' / '把筛选误读为蔑视,放大对立情绪'。要具体场景 + 具体后果"],
  "risk_preference": "格式严格:'偏激进/中性/保守:为什么这样取向(基于卡片);如果用户误用,具体风险是什么。' 示例:'偏激进:鼓励在可控压力场景下突破恐惧,优先训练主动性和表达意图;风险在于用户若缺少反馈识别能力,可能把突破误用成硬推。' 禁止只写'需平衡、注意舒适度'"
}

【输出格式】严格 JSON,不要 markdown 代码块标记,不要额外文字。所有字符串值不能包含换行符。"""


def build_user_prompt(view: dict, sampled_cards: list[dict]) -> str:
    head = (
        f"【视角】{view['display_name']}（类型：{view['filter_type']},"
        f"原始卡片总数：{view['card_count']},本次采样：{len(sampled_cards)}）\n"
    )
    if view["filter_type"] == "course":
        head += "（注：该视角的卡片 mentor 字段为 unknown,以课程为单位聚合）\n"

    blocks: list[str] = []
    for i, c in enumerate(sampled_cards, 1):
        lines = [f"━━ 卡片 {i} ━━"]
        if c.get("concept"):
            lines.append(f"概念：{c['concept']}")
        if c.get("core_claim"):
            lines.append(f"核心观点：{c['core_claim']}")
        tags = c.get("tags") or []
        if tags:
            lines.append(f"标签：{ '、'.join(str(t) for t in tags) }")
        scen = c.get("applicable_scenarios") or []
        if scen:
            lines.append(f"适用场景：{ '；'.join(str(s) for s in scen) }")
        moves = c.get("actionable_moves") or []
        if moves:
            lines.append(f"可执行动作：{ '；'.join(str(m) for m in moves) }")
        miss = c.get("common_mistakes") or []
        if miss:
            lines.append(f"常见误区：{ '；'.join(str(m) for m in miss) }")
        risks = c.get("risk_notes") or []
        if risks:
            lines.append(f"风险/边界：{ '；'.join(str(r) for r in risks) }")
        if c.get("card_type"):
            lines.append(f"卡片类型：{c['card_type']}")
        if c.get("safety_level"):
            lines.append(f"安全等级：{c['safety_level']}")
        if c.get("source_course"):
            lines.append(f"来源课程：{c['source_course']}")
        blocks.append("\n".join(lines))

    body = "\n\n".join(blocks)
    tail = (
        "\n\n请基于以上卡片输出该视角的导师判断模型 JSON,严格遵循 SYSTEM 中的字段定义。\n"
        "再次强调:这是导师思想蒸馏,保留锋利判断和原始立场,不要洗成温和情感咨询师。\n"
        "raw_stance 是导师原始观点,禁止温和化改写;misuse_risks 是误用风险,要和原始立场区分开。\n"
        "would_disagree_with 必须有卡片依据且锋利具体;risk_preference 必须含'误用具体风险'。\n"
        "不要使用卡片外的学术心理学包装词,优先复用卡片体系内的术语。\n"
        "**严禁照抄 SYSTEM 中任何示例句**:raw_stance 必须从该视角的 signature_concepts / core_topics / core_claim 切入,"
        "整段至少包含 2 个该视角独有概念,与其他视角不得共用通用句式。"
    )
    return head + "\n" + body + tail


# ─── 视角内卡片筛选 ────────────────────────────────────────
def _cards_for_view(all_cards: list[dict], view: dict) -> list[dict]:
    ft, fv = view["filter_type"], view["filter_value"]
    if ft == "mentor":
        return [c for c in all_cards if c.get("mentor") == fv]
    if ft == "course":
        return [
            c for c in all_cards
            if c.get("source_course") == fv
            and (c.get("mentor") or "unknown") == "unknown"
        ]
    if ft == "mentor_aggregate":
        # 委托给 service 层的聚合实现,保持单一定义来源
        from services.mentor_knowledge_service import _cards_for_aggregate
        return _cards_for_aggregate(all_cards, fv)
    return []


# ─── 代表性采样 ────────────────────────────────────────────
def _representative_sample(cards: list[dict], n: int) -> list[dict]:
    """
    代表性采样,保证：
      1) 高 relevance_score 优先
      2) 高频 tags 与 concept 各覆盖至少一张
      3) 多 source_course 覆盖
      4) 多 card_type 覆盖
      5) 含 medium/high safety 卡片(若存在,最多占 15%)
    返回去重后的 ≤ n 张卡。
    """
    if not cards:
        return []
    if len(cards) <= n:
        return list(cards)

    picked: list[dict] = []
    picked_ids: set = set()

    def add(card):
        cid = card.get("card_id")
        if cid and cid not in picked_ids:
            picked.append(card)
            picked_ids.add(cid)

    # 1) Top by relevance_score
    by_rel = sorted(
        cards,
        key=lambda c: (
            -(c.get("relevance_score") or 0),
            c.get("noise_score") or 0,
        ),
    )
    top_quota = max(1, n // 2)
    for c in by_rel[: top_quota]:
        add(c)

    # 2) 高频 tags 覆盖（每个高频 tag 至少 1 张未入选卡）
    tag_counter: Counter = Counter()
    for c in cards:
        for t in (c.get("tags") or []):
            tag_counter[str(t)] += 1
    top_tags = [t for t, _ in tag_counter.most_common(8)]
    for tag in top_tags:
        if len(picked) >= n:
            break
        for c in by_rel:
            if c.get("card_id") in picked_ids:
                continue
            if tag in (c.get("tags") or []):
                add(c)
                break

    # 3) 高频 concept 覆盖
    concept_counter: Counter = Counter(
        (c.get("concept") or "").strip() for c in cards if c.get("concept")
    )
    top_concepts = [t for t, _ in concept_counter.most_common(8) if t]
    for cp in top_concepts:
        if len(picked) >= n:
            break
        for c in by_rel:
            if c.get("card_id") in picked_ids:
                continue
            if (c.get("concept") or "").strip() == cp:
                add(c)
                break

    # 4) source_course 覆盖
    courses_seen: set = {c.get("source_course") for c in picked}
    for c in by_rel:
        if len(picked) >= n:
            break
        sc = c.get("source_course")
        if sc and sc not in courses_seen:
            add(c)
            courses_seen.add(sc)

    # 5) card_type 覆盖
    types_seen: set = {c.get("card_type") for c in picked}
    for c in by_rel:
        if len(picked) >= n:
            break
        ct = c.get("card_type")
        if ct and ct not in types_seen:
            add(c)
            types_seen.add(ct)

    # 6) medium/high safety 卡片少量纳入(最多 15%)
    risk_quota = max(1, n // 7)
    risk_added = 0
    for c in by_rel:
        if risk_added >= risk_quota or len(picked) >= n:
            break
        if c.get("card_id") in picked_ids:
            continue
        if (c.get("safety_level") or "low") in ("medium", "high"):
            add(c)
            risk_added += 1

    # 7) 用剩余高分卡补满
    for c in by_rel:
        if len(picked) >= n:
            break
        if c.get("card_id") not in picked_ids:
            add(c)

    return picked[:n]


def _truncate_to_chars(prompt: str, max_chars: int) -> str:
    if len(prompt) <= max_chars:
        return prompt
    return prompt[: max_chars - 1] + "…"


def _hash_cards(cards: list[dict]) -> str:
    ids = sorted(c.get("card_id", "") for c in cards)
    h = hashlib.sha1("|".join(ids).encode("utf-8")).hexdigest()
    return f"sha1:{h[:16]}"


# ─── 单视角生成 ────────────────────────────────────────────
def generate_one(
    view: dict,
    all_cards: list[dict],
    sample_per_view: int,
    max_input_chars: int,
    min_cards: int,
    existing_profile: dict | None,
    force: bool,
) -> tuple[dict, dict]:
    """返回 (profile_or_skip_record, debug_info)。"""
    pool = _cards_for_view(all_cards, view)

    base = {
        "view_type": view["filter_type"],
        "view_name": view["display_name"],
        "source_card_count": len(pool),
        "sampled_card_count": 0,
        "source_hash": "",
        "insufficient_data": False,
    }

    if len(pool) < min_cards:
        base["insufficient_data"] = True
        base["skip_reason"] = f"卡片数 {len(pool)} < {min_cards}"
        return base, {"skipped": True, "view_id": view["id"]}

    sampled = _representative_sample(pool, sample_per_view)
    src_hash = _hash_cards(sampled)
    base["sampled_card_count"] = len(sampled)
    base["source_hash"] = src_hash

    # 增量跳过：hash 未变 + 已有 profile + 非 force
    if (
        not force
        and existing_profile
        and existing_profile.get("source_hash") == src_hash
        and not existing_profile.get("insufficient_data")
    ):
        merged = dict(existing_profile)
        merged.update(base)
        return merged, {"reused": True, "view_id": view["id"]}

    user_prompt = build_user_prompt(view, sampled)
    user_prompt = _truncate_to_chars(user_prompt, max_input_chars)

    data, err = call_deepseek_json(
        system=SYSTEM_PROMPT,
        user=user_prompt,
        max_tokens=1800,
        temperature=0.3,
    )
    if err or not data:
        base["insufficient_data"] = True
        base["skip_reason"] = f"llm_error: {err or 'empty'}"
        return base, {
            "error": True,
            "view_id": view["id"],
            "err": err,
            "input_chars": len(user_prompt),
        }

    profile = {
        "view_type": view["filter_type"],
        "view_name": view["display_name"],
        "style_archetype": str(data.get("style_archetype") or "").strip(),
        "style": str(data.get("style") or "").strip(),
        "core_topics": [str(x).strip() for x in (data.get("core_topics") or []) if x],
        "signature_concepts": [str(x).strip() for x in (data.get("signature_concepts") or []) if x],
        "typical_moves": [str(x).strip() for x in (data.get("typical_moves") or []) if x],
        "raw_stance": str(data.get("raw_stance") or "").strip(),
        "why_it_works": str(data.get("why_it_works") or "").strip(),
        "default_judgment_bias": str(data.get("default_judgment_bias") or "").strip(),
        "would_disagree_with": [str(x).strip() for x in (data.get("would_disagree_with") or []) if x],
        "edge_or_distinctive_angle": str(data.get("edge_or_distinctive_angle") or "").strip(),
        "strengths": [str(x).strip() for x in (data.get("strengths") or []) if x],
        "blind_spots": [str(x).strip() for x in (data.get("blind_spots") or []) if x],
        "fit_scenarios": [str(x).strip() for x in (data.get("fit_scenarios") or []) if x],
        "not_fit_scenarios": [str(x).strip() for x in (data.get("not_fit_scenarios") or []) if x],
        "misuse_risks": [str(x).strip() for x in (data.get("misuse_risks") or []) if x],
        "risk_preference": str(data.get("risk_preference") or "").strip(),
        "source_card_count": len(pool),
        "sampled_card_count": len(sampled),
        "source_hash": src_hash,
        "insufficient_data": False,
    }
    return profile, {
        "ok": True,
        "view_id": view["id"],
        "input_chars": len(user_prompt),
    }


# ─── 主流程 ────────────────────────────────────────────────
def _load_existing_profiles() -> dict:
    if not OUTPUT_PATH.exists():
        return {}
    try:
        data = json.loads(OUTPUT_PATH.read_text(encoding="utf-8"))
        return (data.get("profiles") or {}) if isinstance(data, dict) else {}
    except Exception:
        return {}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="生成 mentor_profiles.json (P4B)")
    ap.add_argument("--view", default=None, help="仅生成单个 view_id")
    ap.add_argument("--dry-run", action="store_true", help="不写文件")
    ap.add_argument("--force", action="store_true", help="忽略 source_hash 比对")
    ap.add_argument("--min-cards", type=int, default=3)
    ap.add_argument("--sample-per-view", type=int, default=40)
    ap.add_argument("--max-input-chars", type=int, default=8000)
    ap.add_argument("--limit", type=int, default=0, help=">0 时仅处理前 N 个视角")
    args = ap.parse_args(argv)

    cards, err = load_cards()
    if err:
        print(f"[ERROR] 加载 cards.json 失败：{err}")
        return 2
    print(f"[INFO] cards 共 {len(cards)} 张")

    views = list_available_mentor_views(cards)
    if args.view:
        views = [v for v in views if v["id"] == args.view]
        if not views:
            print(f"[ERROR] 未找到 view_id={args.view}")
            return 2
    if args.limit > 0:
        views = views[: args.limit]
    print(f"[INFO] 待生成视角 {len(views)} 个")

    existing = _load_existing_profiles()

    profiles: dict = {}
    skipped: list = []
    errors: list = []
    debug: list = []

    for i, v in enumerate(views, 1):
        vid = v["id"]
        print(f"[{i}/{len(views)}] {vid} (cards={v['card_count']})", flush=True)
        prof, info = generate_one(
            view=v,
            all_cards=cards,
            sample_per_view=args.sample_per_view,
            max_input_chars=args.max_input_chars,
            min_cards=args.min_cards,
            existing_profile=existing.get(vid),
            force=args.force,
        )
        profiles[vid] = prof
        debug.append(info)
        if prof.get("insufficient_data"):
            if info.get("error"):
                errors.append({
                    "view_id": vid,
                    "err": info.get("err"),
                    "skip_reason": prof.get("skip_reason"),
                })
            else:
                skipped.append({
                    "view_id": vid,
                    "reason": prof.get("skip_reason", "insufficient_data"),
                })
        # 控速
        time.sleep(0.3)

    output = {
        "version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": DEEPSEEK_MODEL,
        "profiles": profiles,
        "errors": errors,
        "skipped": skipped,
    }

    report = {
        "generated_at": output["generated_at"],
        "model": DEEPSEEK_MODEL,
        "total_views": len(views),
        "ok_count": sum(1 for p in profiles.values() if not p.get("insufficient_data")),
        "skipped_count": len(skipped),
        "error_count": len(errors),
        "min_cards": args.min_cards,
        "sample_per_view": args.sample_per_view,
        "max_input_chars": args.max_input_chars,
        "force": args.force,
        "view_filter": args.view,
        "debug": debug,
    }

    if args.dry_run:
        print("[DRY-RUN] 不写文件,以下为生成结果摘要：")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if profiles:
            first_vid = next(iter(profiles))
            print(f"\n[DRY-RUN] 第一个视角 {first_vid} profile:")
            print(json.dumps(profiles[first_vid], ensure_ascii=False, indent=2))
        return 0

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    REPORT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[OK] 写入 {OUTPUT_PATH}")
    print(f"[OK] 写入 {REPORT_PATH}")
    print(f"[OK] ok={report['ok_count']}, skipped={report['skipped_count']}, errors={report['error_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())



