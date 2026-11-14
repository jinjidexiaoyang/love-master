"""
Mentor Knowledge Service — 只读加载导师知识库 + 中文关键词检索（v2）
数据源：data/mentor_knowledge/cards.json + chunks.json
不调用 LLM，不改数据文件，不接入现有 knowledge_base 表。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

_CARDS_PATH = Path("data/mentor_knowledge/cards.json")
_CHUNKS_PATH = Path("data/mentor_knowledge/chunks.json")

# ─── 检索权重（strong_hit 字段标记 *）─────────────────────
# strong_hit 字段：concept, tags, core_claim, applicable_scenarios,
#                  common_mistakes, actionable_moves
# weak 字段：example_lines, raw_excerpt（不能单独构成 strong_hit）
_STRONG_FIELDS = {
    "concept": 10,
    "tags": 8,
    "core_claim": 5,
    "applicable_scenarios": 4,
    "common_mistakes": 3,
    "actionable_moves": 3,
}
_WEAK_FIELDS = {
    "example_lines": 2,
    "raw_excerpt": 1,
}
_ALL_FIELDS = {**_STRONG_FIELDS, **_WEAK_FIELDS}

# ─── 默认阈值 ────────────────────────────────────────────
DEFAULT_MIN_SCORE = 12
DEFAULT_TOP_K = 6

# ─── 停用词 ───────────────────────────────────────────────
_STOPWORDS = {
    "什么", "怎么", "如何", "是不是", "为什么", "能不能", "可以",
    "她", "他", "我", "你", "的", "了", "吗", "呢", "啊", "吧",
    "在", "和", "就", "这个", "那个", "一个", "还是", "有没有",
    "是", "不是", "到", "也", "都", "很", "会", "要", "能",
    "对", "把", "被", "让", "给", "从", "比", "跟", "向",
    "做", "去", "来", "过", "着", "得", "地", "而", "但",
}

# ─── 领域词 domain terms ─────────────────────────────────
_DOMAIN_TERMS = {
    "两性", "约会", "社交", "聊天", "关系", "吸引", "暧昧",
    "推进", "升温", "降温", "冷淡", "回复", "自信", "价值",
    "框架", "需求感", "边界", "私密", "清吧", "见面", "搭讪",
    "调情", "舒适度", "信任", "女生", "女人", "男生", "男人",
    "约", "撩", "追", "舔", "IOI", "IOD", "DHV", "PUA",
    "心态", "主导", "张力", "进挪", "kino", "开场",
    "暧昧期", "恋爱", "感情", "喜欢", "表白", "拒绝",
    "分手", "复合", "冷处理", "断联", "朋友圈", "微信",
}

# ─── 同义词映射 ───────────────────────────────────────────
_SYNONYMS: dict[str, list[str]] = {
    "降温": ["回复变慢", "冷淡", "变冷", "不主动", "热度下降", "IOD"],
    "回复变慢": ["降温", "冷淡", "不主动"],
    "私密空间": ["私密场合", "单独空间", "独处", "私密"],
    "清吧": ["约会", "线下见面", "喝酒", "私密场合", "酒吧"],
    "推进": ["升温", "升级", "拉近关系", "关系推进", "进挪"],
    "社交自信": ["自信", "自我价值", "需求感", "内在自信"],
    "框架": ["主导权", "边界", "自证", "frame"],
    "约会": ["见面", "线下", "清吧", "约"],
    "暧昧": ["张力", "暧昧期", "试探"],
    "IOI": ["正向信号", "兴趣指标", "好感信号"],
    "IOD": ["负向信号", "冷淡信号", "降温信号"],
    "需求感": ["低姿态", "追", "舔", "讨好"],
    "价值": ["价值展示", "DHV", "高价值", "吸引力"],
    "心态": ["内核", "mindset", "认知"],
    "开场": ["搭讪", "破冰", "开场白"],
    "冷淡": ["降温", "不回", "敷衍", "已读不回"],
    "舒适度": ["comfort", "安全感", "信任"],
    "边界": ["底线", "框架", "原则"],
    "操控": ["PUA", "控制", "打压"],
}


# ─── 主题级扩展（query expansion / alias）─────────────────
# 用户口语触发词 → 卡片体系内术语
# 触发条件:trigger 必须作为完整子串出现在 query 中(不走 ngram,避免误扩)
# 触发后:扩展词加入检索关键词,且强制视为 domain_matched(不惩罚 min_score)
_TOPIC_EXPANSIONS: dict[str, list[str]] = {
    # ── 向上社交 / 高位人脉 ─────────────────────
    "向上社交": [
        "向上社交", "资源的本质", "解决方案", "给予逻辑",
        "信任建立", "获取智慧", "临时经纪人", "超级助理",
        "通道意识", "存交情", "高位",
    ],
    "向上": [
        "向上社交", "资源的本质", "给予逻辑", "通道意识", "存交情",
    ],
    "资源更强": [
        "向上社交", "资源的本质", "解决方案", "给予逻辑",
        "通道意识", "存交情", "临时经纪人",
    ],
    "认识资源": [
        "向上社交", "资源的本质", "解决方案", "给予逻辑",
        "通道意识", "存交情", "临时经纪人",
    ],
    "比我厉害": [
        "向上社交", "高位", "给予逻辑", "解决方案",
        "通道意识", "信任建立",
    ],
    "比我强": [
        "向上社交", "高位", "给予逻辑", "解决方案",
        "通道意识", "信任建立",
    ],
    "高位": [
        "向上社交", "资源的本质", "给予逻辑", "通道意识", "存交情",
    ],
    "人脉": [
        "向上社交", "通道意识", "存交情", "信任建立",
        "资源的本质", "解决方案",
    ],
    "圈层": [
        "向上社交", "通道意识", "存交情", "高位",
        "信任建立", "资源的本质",
    ],
    "贵人": [
        "向上社交", "存交情", "信任建立", "给予逻辑",
        "通道意识", "临时经纪人",
    ],
    "前辈": [
        "向上社交", "存交情", "给予逻辑", "通道意识",
        "信任建立", "获取智慧",
    ],
    "大佬": [
        "向上社交", "高位", "给予逻辑", "存交情",
        "通道意识", "信任建立",
    ],
    "牛人": [
        "向上社交", "高位", "给予逻辑", "存交情",
        "通道意识",
    ],
    "开局": [
        "开场", "破冰", "切入", "信任建立",
    ],
}


# 主题扩展中所有"扩展目标词"也算作 domain term(豁免 domain gate),
# 避免用户问"向上社交怎么开局"被判为非领域 query。
_TOPIC_DOMAIN_TERMS: set[str] = {
    term for terms in _TOPIC_EXPANSIONS.values() for term in terms
}


def _expand_by_topic(query: str) -> tuple[list[str], bool]:
    """
    主题级扩展。
    - trigger 必须作为完整子串出现在 query 中(原文,不分词)
    - 命中后注入扩展词,并强制 domain_matched=True
    返回 (expanded_keywords, topic_triggered)。
    """
    text = query.lower()
    expanded: list[str] = []
    triggered = False
    for trigger, expansion in _TOPIC_EXPANSIONS.items():
        if trigger.lower() in text:
            triggered = True
            expanded.extend(expansion)
    # 去重保序
    seen: set = set()
    out: list[str] = []
    for kw in expanded:
        if kw not in seen:
            seen.add(kw)
            out.append(kw)
    return out, triggered


# ─── 导师聚合视角配置(跨 course 合集)─────────────────────
# 用途:某些导师的体系散落在多个 source_course 中,且 cards.json 里 mentor 字段
# 多为 unknown,需要按"课程合集"聚合成一个 mentor 视角,以便生成完整画像 +
# 在多导师对比中可整体调用。
#
# 规则:
# - 聚合视角的 view_id = "mentor:<name>",与普通 mentor 视角形态一致
# - 聚合视角与原 course 视角"并存",不替换、不删除原 course
# - 卡片归属:课程命中 _MENTOR_AGGREGATIONS[name] 中任一 source_course 即纳入
# - 默认仅对 mentor=unknown 的卡片做聚合,避免与已存在同名 mentor 视角的卡片重复
# - 聚合视角不会自动从原 course 视角中移除卡片(保留溯源,允许用户在 course 视角
#   下单独查看),但聚合本身去重(同一卡片在聚合内只出现一次)
# 硬编码聚合表默认为空：课程合集聚合规则来自 data/mentor_ingest/admin_config/
# mentor_identity_map.json（aggregate_courses 字段），由使用者在管理端自行配置
# 自己导入的导师。公开示例库的卡片已自带化名归属，无需聚合。
_MENTOR_AGGREGATIONS: dict[str, list[str]] = {}


def _aggregated_courses_set() -> set[str]:
    """所有被聚合视角覆盖的 source_course 集合(用于 list_available_mentor_views 标注)。"""
    s: set[str] = set()
    for courses in _MENTOR_AGGREGATIONS.values():
        s.update(courses)
    return s


def _identity_aggregate_courses_by_real_mentor() -> dict[str, list[str]]:
    """
    从 identity_map 中收集 real_mentor_name → aggregate_courses 的映射。
    与硬编码 _MENTOR_AGGREGATIONS 取并集,允许通过 identity_map 动态扩展聚合规则。
    identity_map 缺失时静默返回 {}。
    """
    out: dict[str, list[str]] = {}
    try:
        from services import mentor_identity_service as _ident  # lazy
        for it in _ident.list_identities():
            real = (it.get("real_mentor_name") or "").strip()
            if not real:
                continue
            courses = [c for c in (it.get("aggregate_courses") or []) if c]
            if not courses:
                continue
            out.setdefault(real, []).extend(courses)
    except Exception:
        return {}
    return out


def _resolve_aggregate_courses(name: str) -> list[str]:
    """硬编码 _MENTOR_AGGREGATIONS + identity_map 中同 real_mentor_name 的 aggregate_courses 去重并集。"""
    seen: set = set()
    out: list[str] = []
    for c in (_MENTOR_AGGREGATIONS.get(name) or []):
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    for c in (_identity_aggregate_courses_by_real_mentor().get(name) or []):
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def _all_aggregate_real_names() -> list[str]:
    """硬编码聚合 + identity_map 聚合所有的 real_mentor_name 去重列表。"""
    seen: set = set()
    out: list[str] = []
    for n in list(_MENTOR_AGGREGATIONS.keys()):
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    for n in list(_identity_aggregate_courses_by_real_mentor().keys()):
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    return out


def _cards_for_aggregate(cards: list[dict], name: str) -> list[dict]:
    """
    取聚合卡片合集:
      (mentor == name) OR (source_course ∈ aggregate_courses(name))
    按 card_id 去重。aggregate_courses 来源 = 硬编码 + identity_map。
    """
    courses = _resolve_aggregate_courses(name)
    course_set = set(courses)
    seen: set = set()
    out: list[dict] = []
    for c in cards:
        is_real_mentor = (c.get("mentor") or "") == name
        is_in_courses = c.get("source_course") in course_set
        if not (is_real_mentor or is_in_courses):
            continue
        cid = c.get("card_id")
        if cid in seen:
            continue
        seen.add(cid)
        out.append(c)
    return out


# ─── 数据加载 ─────────────────────────────────────────────
def load_cards() -> tuple[list[dict], str]:
    if not _CARDS_PATH.exists():
        return [], f"卡片文件不存在：{_CARDS_PATH}"
    try:
        data = json.loads(_CARDS_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            return [], "卡片文件格式错误（非数组）"
        cards = [c for c in data if c.get("include_in_tutor", True)]
        return cards, ""
    except Exception as e:
        return [], f"加载失败：{type(e).__name__}"


def load_chunks() -> dict[str, dict]:
    if not _CHUNKS_PATH.exists():
        return {}
    try:
        data = json.loads(_CHUNKS_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            return {}
        return {c["chunk_id"]: c for c in data if "chunk_id" in c}
    except Exception:
        return {}


def get_chunk_by_id(chunk_id: str) -> dict | None:
    chunks = load_chunks()
    return chunks.get(chunk_id)


# ─── 中文分词 + query 处理 ────────────────────────────────
_RE_PUNCT = re.compile(r"[，。！？、；：""''（）【】《》\s\.\,\!\?\;\:\"\'\(\)\[\]\{\}]+")
_RE_CHINESE_RUN = re.compile(r"[一-鿿]+")
_RE_ALPHA_RUN = re.compile(r"[a-zA-Z0-9]+")


def _extract_primary_keywords(text: str) -> list[str]:
    """
    提取主关键词：整段中文短语（<=6字）+ 3/4-gram + 英文词。
    不生成 2-gram（噪声太大）。
    """
    text = text.lower()
    text = _RE_PUNCT.sub(" ", text)

    keywords: list[str] = []

    for m in _RE_ALPHA_RUN.finditer(text):
        w = m.group()
        if len(w) >= 2:
            keywords.append(w)

    for m in _RE_CHINESE_RUN.finditer(text):
        run = m.group()
        if len(run) < 2:
            continue
        # 整段作为关键词
        if 2 <= len(run) <= 6:
            keywords.append(run)
        # 3/4-gram（不做 2-gram，减少噪声）
        for n in (3, 4):
            for i in range(len(run) - n + 1):
                keywords.append(run[i:i + n])
        # 对长段也保留 2-gram 但只限于 domain_terms 命中的
        if len(run) > 4:
            for i in range(len(run) - 1):
                bigram = run[i:i + 2]
                if bigram in _DOMAIN_TERMS or bigram in _SYNONYMS:
                    keywords.append(bigram)

    return keywords


def _process_query(query: str) -> tuple[list[str], bool]:
    """
    处理用户 query。
    返回 (keywords, domain_matched)。
    domain_matched=True 表示 query 包含至少一个领域词。
    同义词只对主关键词扩展（不对 ngram 碎片扩展）。
    """
    text = query.lower()
    text = _RE_PUNCT.sub(" ", text)

    # 先提取"整段"关键词（用于同义词扩展判断）
    primary_phrases: list[str] = []
    for m in _RE_CHINESE_RUN.finditer(text):
        run = m.group()
        if 2 <= len(run) <= 6:
            primary_phrases.append(run)
    for m in _RE_ALPHA_RUN.finditer(text):
        w = m.group()
        if len(w) >= 2:
            primary_phrases.append(w)

    # 去停用词
    primary_phrases = [p for p in primary_phrases if p not in _STOPWORDS]

    # 同义词扩展（只对 primary_phrases，不对 ngram）
    expanded_from_synonyms: list[str] = []
    for phrase in primary_phrases:
        if phrase in _SYNONYMS:
            expanded_from_synonyms.extend(_SYNONYMS[phrase])
        # 也检查 phrase 是否包含某个 synonym key
        for key, vals in _SYNONYMS.items():
            if len(key) >= 2 and key in phrase and key != phrase:
                expanded_from_synonyms.extend(vals)

    # 完整关键词列表 = primary extraction + synonym expansion
    all_kws = _extract_primary_keywords(query)
    # 去停用词
    all_kws = [kw for kw in all_kws if kw not in _STOPWORDS]
    # 加入同义词
    all_kws.extend(expanded_from_synonyms)

    # 主题级扩展(向上社交 / 高位人脉 等口语 → 卡片体系内术语)
    topic_kws, topic_triggered = _expand_by_topic(query)
    all_kws.extend(topic_kws)

    # 去重保序
    seen = set()
    result = []
    for kw in all_kws:
        if kw not in seen:
            seen.add(kw)
            result.append(kw)

    # domain gate 判断
    domain_matched = any(kw in _DOMAIN_TERMS for kw in result)
    # 也检查 primary_phrases 是否有 domain 词
    if not domain_matched:
        domain_matched = any(p in _DOMAIN_TERMS for p in primary_phrases)
    # 也检查 synonym 扩展后是否有 domain 词
    if not domain_matched:
        domain_matched = any(s in _DOMAIN_TERMS for s in expanded_from_synonyms)
    # 主题扩展命中视为 domain_matched(避免向上社交话题被惩罚)
    if not domain_matched and topic_triggered:
        domain_matched = True
    # 主题扩展词本身也算作 domain term
    if not domain_matched:
        domain_matched = any(kw in _TOPIC_DOMAIN_TERMS for kw in result)

    return result, domain_matched


def _field_text(card: dict, field: str) -> str:
    val = card.get(field, "")
    if isinstance(val, list):
        return " ".join(str(v) for v in val)
    return str(val)


def _score_card(card: dict, query_keywords: list[str]) -> tuple[float, bool]:
    """
    对单张卡片打分。
    返回 (score, has_strong_hit)。
    strong_hit = 至少一个 strong field 被命中。
    """
    score = 0.0
    has_strong_hit = False
    concept_text = _field_text(card, "concept").lower()

    for field, weight in _ALL_FIELDS.items():
        ft = _field_text(card, field).lower()
        if not ft:
            continue

        # raw_excerpt 只用 >= 3 字的关键词匹配（避免 2-gram 噪声）
        min_kw_len = 3 if field == "raw_excerpt" else 2

        field_hits = 0
        for kw in query_keywords:
            if len(kw) < min_kw_len:
                continue
            if kw in ft:
                field_hits += 1
            elif len(kw) >= 4 and ft in kw:
                field_hits += 0.3

        if field_hits > 0:
            score += weight * field_hits
            if field in _STRONG_FIELDS:
                has_strong_hit = True

    # concept 额外 bonus
    for kw in query_keywords:
        if len(kw) >= 2:
            if kw in concept_text:
                score += 6
                has_strong_hit = True
            elif len(concept_text) >= 3 and concept_text in kw:
                score += 4
                has_strong_hit = True

    return score, has_strong_hit


# ─── 检索主函数 ───────────────────────────────────────────
def search_cards(
    query: str,
    top_k: int = DEFAULT_TOP_K,
    min_score: float = DEFAULT_MIN_SCORE,
    cards: list[dict] | None = None,
    mentor: str | None = None,
    course: str | None = None,
    card_type: str | None = None,
    safety_level: str | None = None,
    view_filter: dict | None = None,
) -> tuple[list[dict], list[str], dict]:
    """
    中文关键词检索。
    返回 (filtered_cards, query_keywords, diagnostics)。
    diagnostics: {"domain_matched", "min_score_used", "candidates_before_filter", "candidates_after_filter"}
    view_filter: 由 list_available_mentor_views 返回项的 {"filter_type": "mentor"|"course", "filter_value": str}，
                 与 mentor / course 参数互斥，优先级最高。
    """
    if cards is None:
        cards, _ = load_cards()

    diag = {
        "total_cards": len(cards) if cards else 0,
        "domain_matched": False,
        "min_score_used": min_score,
        "candidates_before_filter": 0,
        "candidates_after_filter": 0,
    }

    if not cards or not query.strip():
        return [], [], diag

    # 过滤
    pool = cards
    if view_filter:
        ft = view_filter.get("filter_type")
        fv = view_filter.get("filter_value")
        if ft == "mentor" and fv:
            pool = [c for c in pool if c.get("mentor") == fv]
        elif ft == "course" and fv:
            # course 视角：仅限 mentor=unknown 的卡片，避免与 mentor 视角重叠
            pool = [c for c in pool
                    if c.get("source_course") == fv
                    and (c.get("mentor") or "unknown") == "unknown"]
        elif ft == "mentor_aggregate" and fv:
            # 聚合 mentor 视角:按 _MENTOR_AGGREGATIONS 聚合多个 source_course
            pool = _cards_for_aggregate(pool, fv)
    if mentor:
        pool = [c for c in pool if c.get("mentor") == mentor]
    if course:
        pool = [c for c in pool if c.get("source_course") == course]
    if card_type:
        pool = [c for c in pool if c.get("card_type") == card_type]
    if safety_level:
        pool = [c for c in pool if c.get("safety_level") == safety_level]

    query_keywords, domain_matched = _process_query(query)
    diag["domain_matched"] = domain_matched

    if not query_keywords:
        return [], [], diag

    # 如果 query 不包含领域词，提高阈值
    effective_min_score = min_score if domain_matched else min_score * 1.5
    diag["min_score_used"] = effective_min_score

    # 打分
    scored: list[tuple[float, bool, dict]] = []
    for card in pool:
        s, strong = _score_card(card, query_keywords)
        if s > 0:
            scored.append((s, strong, card))

    scored.sort(key=lambda x: -x[0])
    diag["candidates_before_filter"] = len(scored)

    # 过滤：score >= effective_min_score AND has_strong_hit
    # 非领域 query 额外要求：必须有 concept/tags 强命中
    results = []
    for s, strong, card in scored:
        if s < effective_min_score:
            break  # sorted desc, 后面都更低
        if not strong:
            continue
        if not domain_matched:
            # 非领域 query：只有 concept/tags 命中才放行
            concept_ft = _field_text(card, "concept").lower()
            tags_ft = _field_text(card, "tags").lower()
            concept_or_tags_hit = any(
                kw in concept_ft or kw in tags_ft
                for kw in query_keywords if len(kw) >= 2
            )
            if not concept_or_tags_hit:
                continue
        card_copy = dict(card)
        card_copy["_search_score"] = round(s, 1)
        card_copy["_strong_hit"] = True
        results.append(card_copy)
        if len(results) >= top_k:
            break

    diag["candidates_after_filter"] = len(results)
    return results, query_keywords, diag


# ─── context 构建 ─────────────────────────────────────────
def build_context_from_cards(
    cards: list[dict],
    chunks: dict[str, dict] | None = None,
    max_cards: int = 6,
    group_title: str | None = None,
) -> str:
    if not cards:
        if group_title:
            return f"【{group_title}】\n（未找到相关导师知识卡片）"
        return "（未找到相关导师知识卡片）"

    if chunks is None:
        chunks = load_chunks()

    # 兜底脱敏:即使卡片来自正式库(可能含真实导师名/课程名),
    # 进 LLM prompt 前一律走 redact_card_for_public + 真名替换。
    # identity_map 缺失时退化为原数据。
    try:
        from services.mentor_anonymize_service import build_anonymizer, redact_text
        _anon = build_anonymizer()
    except Exception:
        _anon = None

    lines: list[str] = []
    if group_title:
        lines.append(f"【{group_title}】")
    for i, card in enumerate(cards[:max_cards], start=1):
        card = redact_card_for_public(card)
        lines.append(f"═══ 导师卡片 {i} ═══")
        lines.append(f"概念：{card.get('concept', '')}")
        lines.append(f"核心观点：{card.get('core_claim', '')}")

        moves = card.get("actionable_moves") or []
        if moves:
            lines.append(f"可执行动作：{'；'.join(moves)}")

        mistakes = card.get("common_mistakes") or []
        if mistakes:
            lines.append(f"常见误区：{'；'.join(mistakes)}")

        risks = card.get("risk_notes") or []
        if risks:
            lines.append(f"风险/边界：{'；'.join(risks)}")

        examples = card.get("example_lines") or []
        if examples:
            lines.append(f"示例话术：{'；'.join(examples)}")

        # 来源行只显示 public_label,不暴露 source_file / 真实课程名
        src_label = public_label_for_card(card) or ""
        lines.append(f"来源：{src_label}")

        excerpt = card.get("raw_excerpt", "")
        if not excerpt:
            cid = card.get("chunk_id", "")
            chunk = chunks.get(cid)
            if chunk:
                excerpt = chunk.get("text", "")[:300]
        if excerpt and _anon is not None:
            excerpt = redact_text(_anon, excerpt)
        if excerpt:
            lines.append(f"原文摘录：{excerpt[:300]}")

        safety = card.get("safety_level", "low")
        if safety in ("medium", "high"):
            lines.append(f"⚠ 安全等级：{safety}")

        lines.append("")

    return "\n".join(lines)


# ─── 导师/课程视角列表 ────────────────────────────────────
def list_available_mentor_views(cards: list[dict] | None = None) -> list[dict]:
    """
    聚合可用的"导师/课程视角"选项。
    规则：
      - mentor 字段非空且不为 "unknown" → 作为 mentor 视角
      - mentor 为空或 "unknown" → 用 source_course 作为 course 视角（同名 course 合并）
    返回按 card_count 倒序排列：
      [{"id", "display_name", "filter_type", "filter_value", "card_count", "courses": [...]}]
    """
    if cards is None:
        cards, _ = load_cards()
    if not cards:
        return []

    mentor_buckets: dict[str, dict] = {}
    course_buckets: dict[str, dict] = {}

    for card in cards:
        mentor = (card.get("mentor") or "").strip()
        course = (card.get("source_course") or "").strip()

        if mentor and mentor.lower() != "unknown":
            b = mentor_buckets.setdefault(mentor, {"count": 0, "courses": set()})
            b["count"] += 1
            if course:
                b["courses"].add(course)
        else:
            if not course:
                continue
            b = course_buckets.setdefault(course, {"count": 0, "courses": set()})
            b["count"] += 1
            b["courses"].add(course)

    views: list[dict] = []
    for name, b in mentor_buckets.items():
        views.append({
            "id": f"mentor:{name}",
            "display_name": name,
            "filter_type": "mentor",
            "filter_value": name,
            "card_count": b["count"],
            "courses": sorted(b["courses"]),
        })
    for name, b in course_buckets.items():
        views.append({
            "id": f"course:{name}",
            "display_name": name,
            "filter_type": "course",
            "filter_value": name,
            "card_count": b["count"],
            "courses": sorted(b["courses"]),
        })

    # 追加/替换:导师聚合视角(跨 course 合集)。
    # - 来源 = 硬编码 _MENTOR_AGGREGATIONS ∪ identity_map.aggregate_courses
    # - 如果同名真实 mentor 视角已存在(cards.json 中有 mentor==name 的卡片),
    #   也用聚合视角替代它 — 因为聚合视角本就包含了那批真名卡片,
    #   保留两个 view_id 相同的视角会让前台无法区分(并导致旧 course 卡片"消失")。
    # - 原 course 视角不删除、不修改;前台展示层另行决定是否隐藏被覆盖的 course。
    for agg_name in _all_aggregate_real_names():
        agg_id = f"mentor:{agg_name}"
        agg_cards = _cards_for_aggregate(cards, agg_name)
        if not agg_cards:
            continue
        agg_view = {
            "id": agg_id,
            "display_name": agg_name,
            "filter_type": "mentor_aggregate",
            "filter_value": agg_name,
            "card_count": len(agg_cards),
            "courses": sorted({c.get("source_course", "") for c in agg_cards if c.get("source_course")}),
            "is_aggregate": True,
            "aggregate_courses": _resolve_aggregate_courses(agg_name),
        }
        # 替换同 id 的真实 mentor 视角(如果存在),否则追加
        replaced = False
        for i, v in enumerate(views):
            if v.get("id") == agg_id:
                views[i] = agg_view
                replaced = True
                break
        if not replaced:
            views.append(agg_view)

    views.sort(key=lambda v: -v["card_count"])
    return views


# ─── 导师画像（mentor_profiles.json）─────────────────────
_PROFILES_PATH = Path("data/mentor_knowledge/mentor_profiles.json")
_profiles_cache: dict | None = None


def load_mentor_profiles(force_reload: bool = False) -> dict:
    """读取 mentor_profiles.json。文件不存在或解析失败时返回空 dict（静默退化）。"""
    global _profiles_cache
    if _profiles_cache is not None and not force_reload:
        return _profiles_cache
    if not _PROFILES_PATH.exists():
        _profiles_cache = {}
        return _profiles_cache
    try:
        data = json.loads(_PROFILES_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            _profiles_cache = {}
        else:
            _profiles_cache = data
    except Exception:
        _profiles_cache = {}
    return _profiles_cache


def get_view_profile(view_id: str) -> dict | None:
    """根据 view_id（如 'mentor:<化名>' / 'course:<课程名>'）取 profile。"""
    profiles = load_mentor_profiles().get("profiles") or {}
    p = profiles.get(view_id)
    if not p or p.get("insufficient_data"):
        return None
    return p


def get_view_profile_summary(view_id: str, max_chars: int = 240) -> str:
    """
    生成多导师对比 prompt 注入用的视角先验短句。
    优先体现导师差异:风格档位｜原始立场｜独特角度｜默认判断偏向｜风险偏好。
    profile 缺失或证据不足时返回空字符串(调用方静默跳过)。
    """
    p = get_view_profile(view_id)
    if not p:
        return ""

    parts: list[str] = []
    archetype = (p.get("style_archetype") or "").strip()
    if archetype:
        parts.append(f"风格:{archetype}")

    raw_stance = (p.get("raw_stance") or "").strip()
    if raw_stance:
        parts.append(f"原始立场:{raw_stance}")

    edge = (p.get("edge_or_distinctive_angle") or "").strip()
    if edge:
        parts.append(f"独特角度:{edge}")

    bias = (p.get("default_judgment_bias") or "").strip()
    if bias:
        parts.append(f"默认归因:{bias}")

    risk = (p.get("risk_preference") or "").strip()
    if risk:
        parts.append(f"风险偏好:{risk}")

    summary = "；".join(parts)
    if len(summary) > max_chars:
        summary = summary[: max_chars - 1] + "…"
    return summary


def get_view_profile_summary_by_name(name: str, max_chars: int = 240) -> str:
    """
    按 view_name 反查 profile 摘要。
    优先匹配 mentor:<name>，再匹配 course:<name>，再退化到 view_name 完全相等。
    用于多导师对比调用方只持有显示名（mentor_name）的场景。
    """
    if not name:
        return ""
    profiles = load_mentor_profiles().get("profiles") or {}
    # 1) 直接命中 view_id
    for vid in (f"mentor:{name}", f"course:{name}"):
        if vid in profiles:
            return get_view_profile_summary(vid, max_chars)
    # 2) 通过 view_name 反查
    for vid, p in profiles.items():
        if (p.get("view_name") or "") == name and not p.get("insufficient_data"):
            return get_view_profile_summary(vid, max_chars)
    # 3) public_name → real_mentor_name 反查(前台传入的是化名时)
    try:
        from services import mentor_identity_service as _ident
        for it in _ident.list_identities():
            pname = (it.get("public_name") or "").strip()
            if not pname:
                continue
            # 完整化名 "灰狼（约会节奏控制型）" 或单 "灰狼" 都接
            if name == pname or name.startswith(pname + "（") or name.startswith(pname + "("):
                real = (it.get("real_mentor_name") or "").strip()
                if real:
                    for vid in (f"mentor:{real}", f"course:{real}"):
                        if vid in profiles:
                            return get_view_profile_summary(vid, max_chars)
                    for vid, p in profiles.items():
                        if (p.get("view_name") or "") == real and not p.get("insufficient_data"):
                            return get_view_profile_summary(vid, max_chars)
    except Exception:
        pass
    return ""


# ─── 前台脱敏:identity 映射(身份层接入,不改 cards/chunks/profiles)───
# 设计:本模块不直接 import identity_service(避免硬耦合),用 lazy import + 失败兜底。
# 所有函数在 identity_service 不存在或映射缺失时静默退化,返回原始显示。
def _public_view_for(real_mentor: str | None, source_course: str | None) -> dict | None:
    try:
        from services import mentor_identity_service as _ident  # lazy
        return _ident.public_view(real_mentor, source_course)
    except Exception:
        return None


def public_label_for_view(view: dict) -> dict:
    """
    返回前台展示用的 label/sublabel:
      {
        "label": "灰狼（约会节奏控制型）",      # 主标签
        "sublabel": "" or "课程合集",           # 次标签(可选,不暴露真名)
        "is_public": True/False,                # 是否命中映射
      }
    未命中映射:label=view['display_name'](保留原显示名作为 fallback)
    """
    if not view:
        return {"label": "", "sublabel": "", "is_public": False}
    ft = view.get("filter_type")
    fv = view.get("filter_value")

    pv = None
    if ft in ("mentor", "mentor_aggregate"):
        pv = _public_view_for(fv, None)
    elif ft == "course":
        pv = _public_view_for(None, fv)

    if pv:
        name = pv.get("public_name") or ""
        style = pv.get("style_label") or ""
        label = f"{name}（{style}）" if name and style else (name or style)
        return {
            "label": label or view.get("display_name", ""),
            "sublabel": "课程合集" if ft == "mentor_aggregate" else "",
            "is_public": True,
        }
    # fallback:无映射时保留原 display_name
    return {
        "label": view.get("display_name", ""),
        "sublabel": ft or "",
        "is_public": False,
    }


def public_label_for_card(card: dict) -> str:
    """
    返回单张卡片的前台来源标签。
    匹配优先级:mentor → source_course → 兜底原 source_course。
    """
    if not card:
        return ""
    pv = _public_view_for(
        card.get("mentor") or None,
        card.get("source_course") or None,
    )
    if pv:
        name = pv.get("public_name") or ""
        style = pv.get("style_label") or ""
        return f"{name}（{style}）" if name and style else (name or style)
    return card.get("source_course") or ""


def redact_card_for_public(card: dict) -> dict:
    """
    返回卡片的"前台脱敏副本":
      - source_course / source_file 替换为 public_label(命中映射时)
      - mentor 字段替换为 public_name
      - 其他内容字段(concept / core_claim / actionable_moves / raw_excerpt 等)原样保留
    未命中映射时返回原 card 浅拷贝(不改字段)。
    不会影响 cards.json 数据本身。
    """
    if not card:
        return card
    pv = _public_view_for(
        card.get("mentor") or None,
        card.get("source_course") or None,
    )
    if not pv:
        return dict(card)

    name = pv.get("public_name") or ""
    style = pv.get("style_label") or ""
    public_label = f"{name}（{style}）" if name and style else (name or style)

    redacted = dict(card)
    redacted["mentor"] = name or "unknown"
    redacted["source_course"] = public_label or redacted.get("source_course", "")
    # source_file 通常含真实课程名,直接抹掉
    redacted["source_file"] = ""
    return redacted


def list_public_mentor_views(cards: list[dict] | None = None) -> list[dict]:
    """
    前台用的视角列表:
      - 在 list_available_mentor_views 基础上隐藏"被聚合 identity 覆盖"的 course 视角
        (避免用户看到 5 张同名"灰狼" — 聚合 mentor 视角保留作为代表)
      - 每个视角附加 public_label / public_sublabel 字段供 UI 展示
    内部 view_id / filter_type / filter_value 全部不变,检索逻辑零影响。
    """
    raw = list_available_mentor_views(cards)
    if not raw:
        return []

    try:
        from services import mentor_identity_service as _ident
        all_idents = _ident.list_identities()
    except Exception:
        all_idents = []

    covered_courses: set = set()
    for it in all_idents:
        if (it.get("visibility") or "public") != "public":
            continue
        if not (it.get("public_name") or "").strip():
            continue
        for c in (it.get("aggregate_courses") or []):
            if c:
                covered_courses.add(c)

    out: list[dict] = []
    for v in raw:
        if v.get("filter_type") == "course" and v.get("filter_value") in covered_courses:
            continue
        labels = public_label_for_view(v)
        v2 = dict(v)
        v2["public_label"] = labels["label"]
        v2["public_sublabel"] = labels["sublabel"]
        v2["is_public_mapped"] = labels["is_public"]
        out.append(v2)
    return out
