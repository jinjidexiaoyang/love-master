"""
pages/mentor_tutor.py — 导师问答（独立模块）
基于导师课程知识卡片回答两性/约会/关系问题。
不接入 replier/analyzer/strategist/style_engine/DNA。
"""
import streamlit as st

from database import init_db, get_all_contacts, get_contact, get_chat_history
from services.mentor_knowledge_service import (
    load_cards, load_chunks, search_cards, build_context_from_cards,
    list_available_mentor_views, list_public_mentor_views,
    public_label_for_view, public_label_for_card,
)
from agents.mentor_tutor import answer_mentor_question, answer_multi_mentor_question
from utils.ui_helpers import apply_theme, page_header

@st.cache_resource
def setup_db():
    init_db()
setup_db()

colors = apply_theme()

# ─── 主内容 ──────────────────────────────────────────────
page_header("导师问答", "基于导师课程知识库的实战问答")

# 加载卡片
cards_all, cards_err = load_cards()
if cards_err:
    st.warning(f"导师知识库加载异常：{cards_err}")
if not cards_all:
    st.info("导师知识库为空。请先运行 scripts/build_mentor_cards.py 生成卡片。")
    st.stop()

chunks_map = load_chunks()
mentor_views = list_public_mentor_views(cards_all)

# ─── 解答模式选择 ────────────────────────────────────────
st.markdown('<div class="apple-card">', unsafe_allow_html=True)
mode = st.radio(
    "解答模式",
    ["全部导师综合", "指定导师", "多导师对比"],
    horizontal=True,
    key="mentor_mode",
)

selected_view = None
selected_views: list[dict] = []

if mode == "指定导师":
    if not mentor_views:
        st.info("当前知识库未识别到可用的导师/课程视角。")
    else:
        view_labels = [
            f"{v.get('public_label') or v['display_name']}（{v['card_count']} 张）"
            for v in mentor_views
        ]
        idx = st.selectbox(
            "选择导师/课程视角",
            options=list(range(len(mentor_views))),
            format_func=lambda i: view_labels[i],
            key="mentor_view_single",
        )
        selected_view = mentor_views[idx]

elif mode == "多导师对比":
    if len(mentor_views) < 2:
        st.info("可用导师/课程视角不足 2 个，无法进行多导师对比。")
    else:
        st.caption("为控制调用成本，多导师对比最多选择 3 个视角，每个视角最多检索 3 张卡片，并且只调用 1 次模型。")
        view_labels_map = {
            v["id"]: f"{v.get('public_label') or v['display_name']}（{v['card_count']} 张）"
            for v in mentor_views
        }
        try:
            picked_ids = st.multiselect(
                "选择 2-3 个导师/课程视角",
                options=[v["id"] for v in mentor_views],
                format_func=lambda vid: view_labels_map[vid],
                key="mentor_view_multi",
                max_selections=3,
            )
        except TypeError:
            # 旧版 Streamlit 不支持 max_selections，退化为软校验
            picked_ids = st.multiselect(
                "选择 2-3 个导师/课程视角",
                options=[v["id"] for v in mentor_views],
                format_func=lambda vid: view_labels_map[vid],
                key="mentor_view_multi",
            )
        selected_views = [v for v in mentor_views if v["id"] in picked_ids]
st.markdown('</div>', unsafe_allow_html=True)

# ─── 问题输入 ────────────────────────────────────────────
st.markdown('<div class="apple-card">', unsafe_allow_html=True)
question = st.text_input(
    "你的问题",
    placeholder="她约会后回复变慢，是不是降温？/ 第一次清吧约会怎么判断能不能推进？",
    key="mentor_q",
)
user_situation = st.text_area(
    "补充现实情况（可选）",
    placeholder="比如：线下见面、约会场景、她的状态、你的判断...",
    height=80,
    key="mentor_situation",
)
st.markdown('</div>', unsafe_allow_html=True)

# ─── 联系人上下文（可选）────────────────────────────────
with st.expander("关联联系人上下文（可选）", expanded=False):
    contacts = get_all_contacts()
    contact_names = ["不使用"] + [c["name"] for c in contacts]
    sel_contact = st.selectbox("选择联系人", contact_names, key="mentor_contact")

    use_chat_history = False
    use_analysis = False
    if sel_contact != "不使用":
        use_chat_history = st.checkbox("使用最近聊天记录（30 条）", value=False, key="mentor_use_chat")
        use_analysis = st.checkbox("使用最近分析结果", value=False, key="mentor_use_analysis")

# ─── 检索 + 生成 ─────────────────────────────────────────
do_ask = st.button("生成导师解答", type="primary", use_container_width=True, key="mentor_ask")

if do_ask and question.strip():
    # 模式预校验
    if mode == "指定导师" and not selected_view:
        st.warning("请先选择一个导师/课程视角。")
        st.stop()
    if mode == "多导师对比" and len(selected_views) < 2:
        st.warning("多导师对比模式需要选择 2-3 个导师/课程视角。")
        st.stop()
    if mode == "多导师对比" and len(selected_views) > 3:
        st.warning("为了保证输出质量，多导师对比限制选择 3 个视角。请取消多余视角后再生成，本次不会调用模型。")
        st.stop()

    query_text = f"{question} {user_situation}".strip()

    # ─── 检索 ────────────────────────────────────────────
    multi_groups: list[dict] = []  # 多导师对比模式：[{"view": v, "cards": [...], "diag": {...}, "kws": [...]}]
    matched_cards: list[dict] = []
    query_keywords: list[str] = []
    search_diag: dict = {}

    if mode == "全部导师综合":
        matched_cards, query_keywords, search_diag = search_cards(
            query_text, top_k=6, cards=cards_all
        )
    elif mode == "指定导师":
        matched_cards, query_keywords, search_diag = search_cards(
            query_text, top_k=6, cards=cards_all, view_filter=selected_view
        )
    else:  # 多导师对比
        for v in selected_views:
            mcs, kws, diag = search_cards(
                query_text, top_k=3, cards=cards_all, view_filter=v
            )
            multi_groups.append({"view": v, "cards": mcs, "diag": diag, "kws": kws})
        # 关键词在多视角下基本一致，取第一组用于诊断展示
        if multi_groups:
            query_keywords = multi_groups[0]["kws"]

    # ─── 检索诊断 ────────────────────────────────────────
    with st.expander("检索诊断", expanded=False):
        if mode != "多导师对比":
            st.caption(f"导师卡片总数：{search_diag.get('total_cards', 0)}")
            st.caption(f"领域匹配：{'是' if search_diag.get('domain_matched') else '否'}")
            st.caption(f"最低分阈值：{search_diag.get('min_score_used', 0)}")
            st.caption(f"提取关键词：{', '.join(query_keywords[:20]) if query_keywords else '（无）'}")
            st.caption(f"候选卡（过滤前）：{search_diag.get('candidates_before_filter', 0)}")
            st.caption(f"最终返回：{search_diag.get('candidates_after_filter', 0)}")
            if matched_cards:
                for mc in matched_cards:
                    st.caption(f"  · [{mc.get('_search_score', 0)}] {mc.get('concept', '')}")
            elif search_diag.get("candidates_before_filter", 0) > 0:
                st.caption("候选卡分数不足或无强命中，已过滤。")
            else:
                st.caption("未命中导师卡片。可以尝试换成：约会、降温、私密场合、社交自信、框架、推进 等关键词。")
        else:
            st.caption(f"提取关键词：{', '.join(query_keywords[:20]) if query_keywords else '（无）'}")
            for g in multi_groups:
                v = g["view"]
                d = g["diag"]
                _vlabel = v.get("public_label") or v["display_name"]
                st.caption(
                    f"**{_vlabel}**（视角候选：{d.get('candidates_before_filter',0)}，命中：{len(g['cards'])}）"
                )
                for mc in g["cards"]:
                    st.caption(f"  · [{mc.get('_search_score', 0)}] {mc.get('concept', '')}")

    # ─── 卡片上下文 ─────────────────────────────────────
    if mode != "多导师对比":
        card_context = build_context_from_cards(matched_cards, chunks_map, max_cards=6)
    else:
        card_context = ""  # 走分组路径

    # ─── 联系人上下文 ───────────────────────────────────
    contact_context = ""
    analysis_context = ""
    if sel_contact != "不使用":
        contact = get_contact(sel_contact)
        if contact:
            parts = [f"联系人：{sel_contact}",
                     f"关系阶段：{contact.get('stage', '陌生人')}",
                     f"好感度：{contact.get('interest_score', 0)}/10",
                     f"暧昧指数：{contact.get('ambiguity_percent', 0)}%",
                     f"势头：{contact.get('momentum', 'stable')}"]
            contact_context = "\n".join(parts)

        if use_chat_history:
            history = get_chat_history(sel_contact, limit=30)
            if history:
                lines = [f"{'她' if m['role']=='her' else '我'}：{m['content']}" for m in history[-30:]]
                contact_context += "\n\n【近期聊天记录】\n" + "\n".join(lines)

        if use_analysis:
            last_a = st.session_state.get(f"last_analysis_{sel_contact}")
            if last_a and "error" not in last_a:
                a_parts = []
                if last_a.get("analysis_summary"):
                    a_parts.append(f"局面：{last_a['analysis_summary']}")
                if last_a.get("ioi_signals"):
                    a_parts.append(f"正向信号：{'、'.join(last_a['ioi_signals'])}")
                if last_a.get("iod_signals"):
                    a_parts.append(f"风险信号：{'、'.join(last_a['iod_signals'])}")
                if last_a.get("opportunities"):
                    a_parts.append(f"机会点：{'；'.join(last_a['opportunities'])}")
                analysis_context = "\n".join(a_parts)
            elif contact:
                la = contact.get("last_analysis", "")
                if la:
                    analysis_context = f"最近分析摘要：{la}"

    # ─── 命中卡片展示 ───────────────────────────────────
    if mode != "多导师对比":
        with st.expander(f"命中导师卡片（{len(matched_cards)} 张）", expanded=False):
            if not matched_cards:
                st.caption("未命中导师知识卡片。")
            for mc in matched_cards:
                safety_badge = ""
                if mc.get("safety_level") in ("medium", "high"):
                    safety_badge = f" · ⚠ {mc['safety_level']}"
                flags = mc.get("quality_flags") or []
                flags_str = f" · [{', '.join(flags)}]" if flags else ""
                _src_label = public_label_for_card(mc)
                st.markdown(
                    f"**{mc.get('concept', '')}** — {mc.get('core_claim', '')[:60]}\n\n"
                    f"<span style='opacity:0.5;font-size:0.8rem'>"
                    f"{_src_label} · {mc.get('card_type', '')}"
                    f"{safety_badge}{flags_str}</span>",
                    unsafe_allow_html=True,
                )
                st.markdown("---")
    else:
        total_hit = sum(len(g["cards"]) for g in multi_groups)
        with st.expander(f"命中导师卡片（共 {total_hit} 张，分 {len(multi_groups)} 组）", expanded=False):
            for g in multi_groups:
                v = g["view"]
                _vlabel = v.get("public_label") or v["display_name"]
                st.markdown(f"**【{_vlabel}】 — {len(g['cards'])} 张**")
                if not g["cards"]:
                    st.caption("（该视角未命中卡片）")
                for mc in g["cards"]:
                    safety_badge = ""
                    if mc.get("safety_level") in ("medium", "high"):
                        safety_badge = f" · ⚠ {mc['safety_level']}"
                    _src_label = public_label_for_card(mc)
                    st.markdown(
                        f"· **{mc.get('concept', '')}** — {mc.get('core_claim', '')[:60]}"
                        f"<span style='opacity:0.5;font-size:0.8rem'> "
                        f"{_src_label}{safety_badge}</span>",
                        unsafe_allow_html=True,
                    )
                st.markdown("---")

    # ─── 调用 LLM ───────────────────────────────────────
    if mode != "多导师对比":
        with st.spinner("导师解答生成中..."):
            result = answer_mentor_question(
                question=question,
                card_context=card_context,
                contact_context=contact_context,
                analysis_context=analysis_context,
                user_situation=user_situation,
            )
    else:
        grouped = []
        for g in multi_groups:
            v = g["view"]
            _vlabel = v.get("public_label") or v["display_name"]
            ctx = build_context_from_cards(
                g["cards"], chunks_map, max_cards=3,
                group_title=_vlabel,
            )
            grouped.append({"mentor_name": _vlabel, "card_context": ctx})
        with st.spinner("多导师对比生成中（单次调用）..."):
            result = answer_multi_mentor_question(
                question=question,
                grouped_card_contexts=grouped,
                contact_context=contact_context,
                analysis_context=analysis_context,
                user_situation=user_situation,
            )

    # ─── 结果渲染 ───────────────────────────────────────
    if result.get("error"):
        st.error(f"生成失败：{result['error']}")
        if result.get("core_answer"):
            st.text(result["core_answer"][:500])
    elif mode != "多导师对比":
        st.markdown('<div class="apple-card">', unsafe_allow_html=True)

        st.markdown(f'<div style="font-size:1rem;line-height:1.7;margin-bottom:12px">{result.get("core_answer", "")}</div>', unsafe_allow_html=True)

        reasoning = result.get("reasoning", "")
        if reasoning:
            st.markdown(f'<div style="font-size:0.88rem;opacity:0.7;margin-bottom:12px">💡 {reasoning}</div>', unsafe_allow_html=True)

        actions = result.get("action_plan") or []
        if actions:
            st.markdown('<div style="font-size:0.75rem;font-weight:600;opacity:0.5;text-transform:uppercase;margin-bottom:6px">动作建议</div>', unsafe_allow_html=True)
            for a in actions:
                st.markdown(f'<div style="font-size:0.9rem;padding:2px 0;color:{colors["GREEN"]}">→ {a}</div>', unsafe_allow_html=True)

        donts = result.get("what_not_to_do") or []
        if donts:
            st.markdown('<div style="font-size:0.75rem;font-weight:600;opacity:0.5;text-transform:uppercase;margin:10px 0 6px 0">不要做</div>', unsafe_allow_html=True)
            for d in donts:
                st.markdown(f'<div style="font-size:0.9rem;padding:2px 0;opacity:0.8">✗ {d}</div>', unsafe_allow_html=True)

        examples = result.get("example_lines") or []
        if examples:
            st.markdown('<div style="font-size:0.75rem;font-weight:600;opacity:0.5;text-transform:uppercase;margin:10px 0 6px 0">话术参考</div>', unsafe_allow_html=True)
            for ex in examples:
                st.markdown(f'<div style="font-size:0.9rem;padding:2px 0">「{ex}」</div>', unsafe_allow_html=True)

        risk = result.get("risk_boundary", "")
        if risk:
            st.markdown(f'<div style="margin-top:10px;padding:8px 12px;background:rgba(255,150,0,0.08);border-radius:6px;font-size:0.85rem">⚠ {risk}</div>', unsafe_allow_html=True)

        concepts = result.get("related_mentor_concepts") or []
        if concepts:
            st.markdown(f'<div style="margin-top:10px;font-size:0.8rem;opacity:0.5">相关概念：{"、".join(concepts)}</div>', unsafe_allow_html=True)

        st.markdown('</div>', unsafe_allow_html=True)

        refs = result.get("source_refs") or []
        if refs or matched_cards:
            with st.expander("引用来源", expanded=False):
                if refs:
                    for r in refs:
                        st.caption(f"· {r}")
                for mc in matched_cards[:4]:
                    excerpt = mc.get("raw_excerpt", "")
                    if excerpt:
                        _src_label = public_label_for_card(mc)
                        st.caption(f"**{mc.get('concept','')}** ({_src_label})")
                        st.text(excerpt[:200])
    else:
        # 多导师对比渲染
        st.markdown('<div class="apple-card">', unsafe_allow_html=True)

        # 总判断
        st.markdown(
            f'<div style="font-size:1rem;line-height:1.7;margin-bottom:14px">{result.get("core_answer", "")}</div>',
            unsafe_allow_html=True,
        )

        # 各导师视角
        views_out = result.get("mentor_views") or []
        if views_out:
            st.markdown(
                '<div style="font-size:0.75rem;font-weight:600;opacity:0.5;text-transform:uppercase;margin:6px 0 8px 0">各导师/课程视角</div>',
                unsafe_allow_html=True,
            )
            cols = st.columns(len(views_out))
            for col, mv in zip(cols, views_out):
                with col:
                    st.markdown(f"**{mv.get('mentor', '')}**")
                    st.markdown(
                        f"<div style='font-size:0.88rem;line-height:1.6;margin-bottom:6px'>{mv.get('judgment', '')}</div>",
                        unsafe_allow_html=True,
                    )
                    rsn = mv.get("reasoning", "")
                    if rsn:
                        st.markdown(
                            f"<div style='font-size:0.8rem;opacity:0.65;margin-bottom:6px'>💡 {rsn}</div>",
                            unsafe_allow_html=True,
                        )
                    for a in mv.get("action_plan") or []:
                        st.markdown(
                            f"<div style='font-size:0.85rem;color:{colors['GREEN']}'>→ {a}</div>",
                            unsafe_allow_html=True,
                        )
                    risk = mv.get("risk", "")
                    if risk:
                        st.markdown(
                            f"<div style='font-size:0.8rem;opacity:0.7;margin-top:6px'>⚠ {risk}</div>",
                            unsafe_allow_html=True,
                        )

        # 共同点 / 分歧
        common = result.get("common_ground") or []
        if common:
            st.markdown(
                '<div style="font-size:0.75rem;font-weight:600;opacity:0.5;text-transform:uppercase;margin:14px 0 6px 0">共同点</div>',
                unsafe_allow_html=True,
            )
            for c in common:
                st.markdown(f'<div style="font-size:0.9rem;padding:2px 0">· {c}</div>', unsafe_allow_html=True)

        diffs = result.get("disagreements") or []
        if diffs:
            st.markdown(
                '<div style="font-size:0.75rem;font-weight:600;opacity:0.5;text-transform:uppercase;margin:10px 0 6px 0">分歧点</div>',
                unsafe_allow_html=True,
            )
            for d in diffs:
                st.markdown(f'<div style="font-size:0.9rem;padding:2px 0">↔ {d}</div>', unsafe_allow_html=True)

        # 综合建议
        final = result.get("final_recommendation", "")
        if final:
            st.markdown(
                '<div style="font-size:0.75rem;font-weight:600;opacity:0.5;text-transform:uppercase;margin:14px 0 6px 0">综合建议</div>',
                unsafe_allow_html=True,
            )
            st.markdown(
                f'<div style="font-size:0.95rem;line-height:1.7">{final}</div>',
                unsafe_allow_html=True,
            )

        # 最终行动
        actions = result.get("action_plan") or []
        if actions:
            st.markdown(
                '<div style="font-size:0.75rem;font-weight:600;opacity:0.5;text-transform:uppercase;margin:10px 0 6px 0">最终行动</div>',
                unsafe_allow_html=True,
            )
            for a in actions:
                st.markdown(
                    f'<div style="font-size:0.9rem;padding:2px 0;color:{colors["GREEN"]}">→ {a}</div>',
                    unsafe_allow_html=True,
                )

        donts = result.get("what_not_to_do") or []
        if donts:
            st.markdown(
                '<div style="font-size:0.75rem;font-weight:600;opacity:0.5;text-transform:uppercase;margin:10px 0 6px 0">不要做</div>',
                unsafe_allow_html=True,
            )
            for d in donts:
                st.markdown(f'<div style="font-size:0.9rem;padding:2px 0;opacity:0.8">✗ {d}</div>', unsafe_allow_html=True)

        examples = result.get("example_lines") or []
        if examples:
            st.markdown(
                '<div style="font-size:0.75rem;font-weight:600;opacity:0.5;text-transform:uppercase;margin:10px 0 6px 0">话术参考</div>',
                unsafe_allow_html=True,
            )
            for ex in examples:
                st.markdown(f'<div style="font-size:0.9rem;padding:2px 0">「{ex}」</div>', unsafe_allow_html=True)

        risk = result.get("risk_boundary", "")
        if risk:
            st.markdown(
                f'<div style="margin-top:10px;padding:8px 12px;background:rgba(255,150,0,0.08);border-radius:6px;font-size:0.85rem">⚠ {risk}</div>',
                unsafe_allow_html=True,
            )

        st.markdown('</div>', unsafe_allow_html=True)

elif do_ask and not question.strip():
    st.warning("请输入问题")
