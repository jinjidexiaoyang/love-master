"""
mentor_anonymize_service — 正式知识库导师身份脱敏共享逻辑

设计原则:
  - 只在客户端可见字段上替换:
      mentor / source_course / source_file / course_name / title /
      relative_path / source_dir / raw_excerpt
  - 真实值挪到 admin_only 字段(real_mentor / source_real_course /
    source_real_file / source_real_relative_path 等),仅管理端可见。
  - 不破坏 batch_id / item_id / chunk_id / card_id / source_url 等追踪字段。
  - 不修改 cards/chunks 之外的内容字段(concept / core_claim / actionable_moves
    等)结构,仅在文本中做"真实导师名/课程名 → public_name/style_label"替换。
  - identity_map 缺失时静默退化,返回原样数据(由调用方决定是否报错)。

被以下调用方共享:
  - scripts/anonymize_mentor_knowledge.py (一次性迁移正式库)
  - scripts/approve_mentor_ingest_batch.py (合并新 batch 前自动脱敏)
  - scripts/generate_mentor_profiles.py 入参侧(可选,LLM 入参不喂真名)
"""
from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from services import mentor_identity_service as ident


# 概念词/派系名白名单：这些词恰好被登记为某导师的 real_mentor_name/alias，
# 但本质是行业术语（阿尔法=alpha male 概念、红丸=red pill 理论），不是 PII。
# 机制：允许它们做 mentor/course 字段的【精确映射】（by_real/by_alias 不受影响），
# 但【排除】自由文本子串替换与审计扫描，避免把正文里的
# "阿尔法男人""红丸理论" 误改成化名 / 误报真名泄漏。
CONCEPT_TERM_WHITELIST = {"阿尔法", "红丸"}


# ─── Anonymizer 构造 ───────────────────────────────────────
@dataclass
class Anonymizer:
    """缓存 identity_map 派生的查表与替换正则。"""
    identities: list[dict] = field(default_factory=list)
    by_real: dict[str, dict] = field(default_factory=dict)
    by_course: dict[str, dict] = field(default_factory=dict)
    by_alias: dict[str, dict] = field(default_factory=dict)
    text_replacements: list[tuple[re.Pattern, str]] = field(default_factory=list)


def build_anonymizer() -> Anonymizer:
    items = [it for it in ident.list_identities()
             if (it.get("visibility") or "public") == "public"]
    a = Anonymizer(identities=items)

    for it in items:
        real = (it.get("real_mentor_name") or "").strip()
        if real:
            a.by_real[real] = it
        for c in (it.get("aggregate_courses") or []):
            c = (c or "").strip()
            if c:
                a.by_course[c] = it
        for alias in (it.get("aliases") or []):
            alias = (alias or "").strip()
            if alias:
                a.by_alias[alias] = it

    # text_replacements:出现在 raw_excerpt / title / 文本字段中的"裸真名"。
    # 优先按长度倒序替换,避免短串先吃掉长串。
    candidate_pairs: list[tuple[str, str]] = []
    for it in items:
        public = (it.get("public_name") or "").strip()
        if not public:
            continue
        real = (it.get("real_mentor_name") or "").strip()
        if real and real != public and real not in CONCEPT_TERM_WHITELIST:
            candidate_pairs.append((real, public))
        for alias in (it.get("aliases") or []):
            alias = (alias or "").strip()
            if alias and alias != public and alias not in CONCEPT_TERM_WHITELIST:
                candidate_pairs.append((alias, public))
        for c in (it.get("aggregate_courses") or []):
            c = (c or "").strip()
            if c and c != public:
                # 课程名替换成 style_label 而非 public_name,避免课程感丢失
                style = (it.get("style_label") or public).strip() or public
                candidate_pairs.append((c, style))

    seen: set = set()
    candidate_pairs.sort(key=lambda p: -len(p[0]))
    for src, dst in candidate_pairs:
        if src in seen:
            continue
        seen.add(src)
        a.text_replacements.append(
            (re.compile(re.escape(src)), dst)
        )

    return a


# ─── 字符串文本兜底替换 ─────────────────────────────────────
def redact_text(a: Anonymizer, text: str) -> str:
    """对自由文本字段(raw_excerpt / view_name 等)做 真名/课程名 → public 的替换。"""
    if not text or not a.text_replacements:
        return text
    out = text
    for pat, rep in a.text_replacements:
        out = pat.sub(rep, out)
    return out


def _lookup(a: Anonymizer, mentor: str | None,
            course: str | None) -> dict | None:
    if mentor:
        m = a.by_real.get(mentor.strip())
        if m:
            return m
        m = a.by_alias.get(mentor.strip())
        if m:
            return m
    if course:
        c = a.by_course.get(course.strip())
        if c:
            return c
    return None


# ─── cards.json 脱敏 ───────────────────────────────────────
def anonymize_card(a: Anonymizer, card: dict) -> dict:
    if not card or not isinstance(card, dict):
        return card
    out = deepcopy(card)
    real_mentor = (out.get("mentor") or "").strip()
    real_course = (out.get("source_course") or "").strip()
    real_file = (out.get("source_file") or "").strip()
    real_title = (out.get("title") or "").strip()

    it = _lookup(a, real_mentor, real_course)
    if not it:
        # 未命中映射:不动客户端字段,但仍然做 raw_excerpt 文本兜底
        if out.get("raw_excerpt"):
            out["raw_excerpt"] = redact_text(a, out["raw_excerpt"])
        return out

    public = (it.get("public_name") or "").strip()
    style = (it.get("style_label") or "").strip()

    # admin_only 副本(后台溯源)
    out["admin_only"] = {
        "real_mentor": real_mentor,
        "source_real_course": real_course,
        "source_real_file": real_file,
        "source_real_title": real_title,
        "public_mentor_id": it.get("public_mentor_id", ""),
    }

    # 客户端可见字段全部脱敏
    if public:
        out["mentor"] = public
    if style:
        out["source_course"] = style
    elif public:
        out["source_course"] = public
    out["source_file"] = ""
    if real_title:
        out["title"] = redact_text(a, real_title)

    # raw_excerpt 文本里如果含真实导师名,做替换(保留卡片内容)
    if out.get("raw_excerpt"):
        out["raw_excerpt"] = redact_text(a, out["raw_excerpt"])

    out["_anonymized"] = True
    return out


# ─── chunks.json 脱敏 ──────────────────────────────────────
def anonymize_chunk(a: Anonymizer, chunk: dict) -> dict:
    if not chunk or not isinstance(chunk, dict):
        return chunk
    out = deepcopy(chunk)
    real_mentor = (out.get("mentor") or "").strip()
    real_course = (out.get("course_name") or "").strip()
    real_file = (out.get("source_file") or "").strip()
    real_rel = (out.get("relative_path") or "").strip()
    real_root = (out.get("source_root") or "").strip()
    real_dir = (out.get("source_dir") or "").strip()

    it = _lookup(a, real_mentor, real_course)
    if not it:
        # 未命中:text 字段也做兜底替换
        if out.get("text"):
            out["text"] = redact_text(a, out["text"])
        return out

    public = (it.get("public_name") or "").strip()
    style = (it.get("style_label") or "").strip()

    out["admin_only"] = {
        "real_mentor": real_mentor,
        "source_real_course": real_course,
        "source_real_file": real_file,
        "source_real_relative_path": real_rel,
        "source_real_root": real_root,
        "source_real_dir": real_dir,
        "public_mentor_id": it.get("public_mentor_id", ""),
    }

    if public:
        out["mentor"] = public
    if style:
        out["course_name"] = style
    elif public:
        out["course_name"] = public

    # 路径字段:保留 relative_path / source_dir 在 admin_only,客户端置空
    out["source_file"] = ""
    out["relative_path"] = ""
    out["source_dir"] = ""

    if out.get("text"):
        out["text"] = redact_text(a, out["text"])

    out["_anonymized"] = True
    return out


# ─── mentor_profiles.json 脱敏 ─────────────────────────────
def _public_view_id(a: Anonymizer, real_view_id: str) -> str:
    """mentor:<真名> → mentor:<化名>;course:<真课程名> → mentor:<化名>(聚合到 mentor)。"""
    if not real_view_id:
        return real_view_id
    if real_view_id.startswith("mentor:"):
        name = real_view_id.split(":", 1)[1]
        it = a.by_real.get(name) or a.by_alias.get(name)
        if it and (it.get("public_name") or "").strip():
            return f"mentor:{it['public_name'].strip()}"
        return real_view_id
    if real_view_id.startswith("course:"):
        name = real_view_id.split(":", 1)[1]
        it = a.by_course.get(name)
        if it and (it.get("public_name") or "").strip():
            # course 视角并入 mentor 视角(避免前台仍能拼出真实课程名)
            return f"mentor:{it['public_name'].strip()}"
        return real_view_id
    return real_view_id


def anonymize_profile_entry(a: Anonymizer, view_id: str, p: dict) -> dict:
    if not isinstance(p, dict):
        return p
    out = deepcopy(p)
    real_view_name = (out.get("view_name") or "").strip()
    it = None
    if view_id.startswith("mentor:"):
        it = a.by_real.get(view_id[len("mentor:"):]) or a.by_alias.get(view_id[len("mentor:"):])
    elif view_id.startswith("course:"):
        it = a.by_course.get(view_id[len("course:"):])
    if it is None and real_view_name:
        it = a.by_real.get(real_view_name) or a.by_alias.get(real_view_name) \
            or a.by_course.get(real_view_name)

    if it is None:
        # 未命中:仍对常见文本字段做兜底替换
        for f in ("style", "raw_stance", "why_it_works",
                  "default_judgment_bias", "edge_or_distinctive_angle",
                  "risk_preference", "view_name", "style_archetype"):
            if isinstance(out.get(f), str):
                out[f] = redact_text(a, out[f])
        for f in ("core_topics", "signature_concepts", "typical_moves",
                  "would_disagree_with", "strengths", "blind_spots",
                  "fit_scenarios", "not_fit_scenarios", "misuse_risks"):
            v = out.get(f)
            if isinstance(v, list):
                out[f] = [redact_text(a, str(x)) for x in v]
        return out

    public = (it.get("public_name") or "").strip()
    style = (it.get("style_label") or "").strip()

    out["admin_only"] = {
        "real_mentor": (it.get("real_mentor_name") or "").strip(),
        "real_view_name": real_view_name,
        "public_mentor_id": it.get("public_mentor_id", ""),
    }
    if public:
        out["view_name"] = public
    if style and not (out.get("style_archetype") or "").strip():
        out["style_archetype"] = style
    # view_type 改为 mentor(聚合视角)
    out["view_type"] = "mentor"

    # 全字段文本兜底替换(避免 LLM 蒸馏内容里残留真名)
    for f in ("style", "raw_stance", "why_it_works",
              "default_judgment_bias", "edge_or_distinctive_angle",
              "risk_preference", "style_archetype"):
        if isinstance(out.get(f), str):
            out[f] = redact_text(a, out[f])
    for f in ("core_topics", "signature_concepts", "typical_moves",
              "would_disagree_with", "strengths", "blind_spots",
              "fit_scenarios", "not_fit_scenarios", "misuse_risks"):
        v = out.get(f)
        if isinstance(v, list):
            out[f] = [redact_text(a, str(x)) for x in v]

    out["_anonymized"] = True
    return out


def anonymize_profiles_doc(a: Anonymizer, doc: dict) -> dict:
    if not isinstance(doc, dict):
        return doc
    out = deepcopy(doc)
    profiles = out.get("profiles") or {}
    new_profiles: dict = {}

    # 先对现有视角逐个脱敏
    for vid, p in profiles.items():
        new_vid = _public_view_id(a, vid)
        new_p = anonymize_profile_entry(a, vid, p)
        # 若脱敏后多个 vid 映射到同一 new_vid,保留 source_card_count 大的那条
        if new_vid in new_profiles:
            old = new_profiles[new_vid]
            if (new_p.get("source_card_count") or 0) > (old.get("source_card_count") or 0):
                new_profiles[new_vid] = new_p
        else:
            new_profiles[new_vid] = new_p

    out["profiles"] = new_profiles

    # errors / skipped 列表里也兜底替换字符串 view_id
    for k in ("errors", "skipped"):
        arr = out.get(k) or []
        for entry in arr:
            if isinstance(entry, dict) and "view_id" in entry:
                entry["view_id"] = _public_view_id(a, entry["view_id"])
    return out


def anonymize_profiles_report(a: Anonymizer, doc: dict) -> dict:
    if not isinstance(doc, dict):
        return doc
    out = deepcopy(doc)
    debug = out.get("debug") or []
    for entry in debug:
        if isinstance(entry, dict) and "view_id" in entry:
            entry["view_id"] = _public_view_id(a, entry["view_id"])
    if isinstance(out.get("view_filter"), str):
        out["view_filter"] = _public_view_id(a, out["view_filter"]) \
            if out["view_filter"] else out["view_filter"]
    return out


# ─── 简易 audit:扫正式库残留真名 ───────────────────────────
def find_real_name_leaks(a: Anonymizer, items: list[dict],
                         client_visible_fields: list[str]) -> list[dict]:
    """
    扫描 items(cards / chunks / profiles_entries),返回每条命中的:
      [{ index, field, hit }]
    用于 apply 后做"是否还残留真名"的健康检查。
    """
    if not a.identities:
        return []
    needles: list[str] = []
    for it in a.identities:
        for k in ("real_mentor_name",):
            v = (it.get(k) or "").strip()
            if v:
                needles.append(v)
        for alias in (it.get("aliases") or []):
            v = (alias or "").strip()
            if v:
                needles.append(v)
        for c in (it.get("aggregate_courses") or []):
            v = (c or "").strip()
            if v:
                needles.append(v)
    # 概念词白名单：阿尔法=alpha male 概念、红丸=red pill 理论，均为行业术语非 PII，
    # 从自由文本残留扫描中排除（精确字段映射仍由 by_real/by_alias 负责，不受影响）。
    # 真实人名的自由文本扫描能力保留（白名单只排这两个概念词）。
    needles = [n for n in needles if n not in CONCEPT_TERM_WHITELIST]
    needles = sorted(set(needles), key=lambda s: -len(s))
    if not needles:
        return []

    out: list[dict] = []
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        for f in client_visible_fields:
            v = item.get(f)
            if isinstance(v, str):
                for n in needles:
                    if n and n in v:
                        out.append({"index": i, "field": f, "hit": n,
                                    "id": item.get("card_id") or item.get("chunk_id") or ""})
                        break
            elif isinstance(v, list):
                joined = "\n".join(str(x) for x in v)
                for n in needles:
                    if n and n in joined:
                        out.append({"index": i, "field": f, "hit": n,
                                    "id": item.get("card_id") or item.get("chunk_id") or ""})
                        break
    return out


# 客户端可见字段定义(供 audit / approve 共用)
CARD_CLIENT_FIELDS = [
    "mentor", "source_course", "source_file", "title",
    "raw_excerpt",
]
CHUNK_CLIENT_FIELDS = [
    "mentor", "course_name", "source_file",
    "relative_path", "source_dir", "text",
]
PROFILE_CLIENT_FIELDS = [
    "view_name", "style", "style_archetype", "raw_stance",
    "why_it_works", "default_judgment_bias", "edge_or_distinctive_angle",
    "risk_preference",
    "core_topics", "signature_concepts", "typical_moves",
    "would_disagree_with", "strengths", "blind_spots",
    "fit_scenarios", "not_fit_scenarios", "misuse_risks",
]
