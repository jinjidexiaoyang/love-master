"""
pages/analysis.py — 聊天分析工作台
"""
import streamlit as st
import json
import os

from database import init_db, get_all_contacts, save_message
from agents.analyzer import analyze_conversation, quick_analyze_message
from agents.replier import generate_reply, generate_opening, evaluate_my_reply, chat_tutor
from agents.strategist import generate_strategy, get_technique_guide
from services.ingestion_service import auto_detect_and_parse, import_chat_to_db
from services.analysis_correction_service import (
    get_corrections, get_correction_hint, add_correction, clear_corrections, is_corrections_file_healthy
)
from utils.scoring import score_from_analysis
from config import RELATIONSHIP_STAGES, GAME_TECHNIQUES
from utils.ui_helpers import apply_theme, page_header

@st.cache_resource
def setup_db():
    init_db()
setup_db()

colors = apply_theme()

with st.sidebar:
    contacts = get_all_contacts()
    contact_names = [c["name"] for c in contacts]
    if contact_names:
        if "analysis_contact" not in st.session_state or st.session_state.analysis_contact not in contact_names:
            st.session_state.analysis_contact = contact_names[0]
        selected = st.selectbox("选择联系人", contact_names,
                                index=contact_names.index(st.session_state.analysis_contact),
                                key="contact_selector")
        st.session_state.analysis_contact = selected
    else:
        st.markdown('<div style="opacity:0.5;font-size:0.85rem">先去「联系人」页添加联系人</div>', unsafe_allow_html=True)

page_header("聊天分析")

contacts = get_all_contacts()
if not contacts:
    st.markdown('<div class="apple-card" style="text-align:center;padding:48px 0;opacity:0.5;">还没有联系人 · 请先去「联系人」页面添加</div>', unsafe_allow_html=True)
    st.stop()

name = st.session_state.get("analysis_contact", contacts[0]["name"])
st.markdown(f'<div class="page-subtitle">当前联系人：{name}</div>', unsafe_allow_html=True)

if not dna_exists or not os.path.exists("data/style_profile.json"):
    st.info("💡 风格核心尚未蒸馏，AI 将使用通用模式。建议先去「风格核心」完成蒸馏。")

tab1, tab2, tab3, tab4 = st.tabs(["导入 · 分析", "回复建议", "开场白", "策略中心"])

# ─── Tab1: 导入分析 ───────────────────────────────────────────

with tab1:
    # ── 手动粘贴聊天记录（粘贴导入 · 分析） ─────────────────────
    with st.expander("粘贴导入 · 分析", expanded=True):
        st.markdown("**粘贴聊天记录，AI 自动分析局面**")
        st.caption("支持：时间戳格式 / 我:/她: 格式 / CSV / JSON")

        chat_text = st.text_area(
            "聊天记录",
            height=200,
            placeholder="2026-04-18 18:16:38 '我'\n今天天气不错\n\n2026-04-18 18:16:47 '对方'\n[语音转文字] 是啊，适合出去走走...",
            label_visibility="collapsed"
        )

        col_btn1, col_btn2 = st.columns([1, 4])
        with col_btn1:
            do_analyze = st.button("分析对话", type="primary", use_container_width=True)
        with col_btn2:
            save_only = st.button("仅保存记录", use_container_width=True)

    if (do_analyze or save_only) and chat_text.strip():
        st.session_state[f"last_chat_text_{name}"] = chat_text
        fmt, messages = auto_detect_and_parse(chat_text, name)
        count = import_chat_to_db(name, messages)
        st.caption(f"已识别为 {fmt} 格式，导入 {count} 条消息")

        if do_analyze and messages:
            with st.spinner("AI 分析中..."):
                result = analyze_conversation(name, messages,
                                              correction_hint=get_correction_hint(name))
                scored = score_from_analysis(result)

            if "error" in result:
                st.error(result["error"])
            else:
                st.session_state[f"last_analysis_{name}"] = result
                st.session_state[f"last_scored_{name}"] = scored

    elif do_analyze and not chat_text.strip():
        # 输入框为空：直接基于数据库历史记录分析
        from database import get_chat_history
        history = get_chat_history(name, limit=50)
        if not history:
            st.warning("暂无历史记录，请先导入聊天记录")
        else:
            with st.spinner("AI 分析中（基于已有记录）..."):
                result = analyze_conversation(name, history,
                                              correction_hint=get_correction_hint(name))
                scored = score_from_analysis(result)
            if "error" in result:
                st.error(result["error"])
            else:
                st.session_state[f"last_analysis_{name}"] = result
                st.session_state[f"last_scored_{name}"] = scored

    # 结果展示区（持久显示，不随按钮消失）
    result = st.session_state.get(f"last_analysis_{name}")
    scored = st.session_state.get(f"last_scored_{name}")

    if result and "error" not in result:
        st.markdown("<hr style='border:none;border-top:1px solid rgba(128,128,128,0.15);margin:16px 0'>", unsafe_allow_html=True)

        # 行1：局面 + 量化指标
        col_a, col_b = st.columns([3, 2])

        with col_a:
            st.markdown('<div class="apple-card">', unsafe_allow_html=True)
            st.markdown('<div style="font-size:0.7rem;font-weight:600;opacity:0.45;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:10px">当前局面</div>', unsafe_allow_html=True)
            st.markdown(f'<div style="font-size:0.95rem;line-height:1.65">{result.get("analysis_summary","")}</div>', unsafe_allow_html=True)

            # 对方可能状态（推断性）
            st.markdown('<div style="font-size:0.7rem;font-weight:600;opacity:0.45;text-transform:uppercase;letter-spacing:0.5px;margin:14px 0 6px 0">对方可能状态 <span style="font-weight:400;font-size:0.68rem;opacity:0.6">· AI 推断</span></div>', unsafe_allow_html=True)
            momentum = result.get("momentum", "stable")
            momentum_map = {"rising": ("↑ 升温中", colors["GREEN"]), "stable": ("→ 平稳", colors.get("TEXT_SEC","#888")), "falling": ("↓ 降温", colors["ORANGE"])}
            m_label, m_color = momentum_map.get(momentum, ("→ 平稳", "#888"))
            st.markdown(f'<span style="color:{m_color};font-size:0.9rem;font-weight:600">{m_label}</span>', unsafe_allow_html=True)
            st.markdown('</div>', unsafe_allow_html=True)

        with col_b:
            st.markdown('<div class="apple-card">', unsafe_allow_html=True)
            st.markdown('<div style="font-size:0.7rem;font-weight:600;opacity:0.45;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:10px">量化指标 <span style="font-weight:400;font-size:0.68rem;opacity:0.6">· 仅供参考</span></div>', unsafe_allow_html=True)

            stage = result.get("relationship_stage", "-")
            interest = scored["interest_score"] if scored else result.get("interest_score", 0)
            ambiguity = scored["ambiguity_percent"] if scored else result.get("ambiguity_percent", 0)

            st.markdown(f'<div class="metric-label">关系阶段</div><div style="font-size:1.2rem;font-weight:700;margin-bottom:10px">{stage}</div>', unsafe_allow_html=True)
            st.markdown(f'<div class="metric-label">好感度</div><div class="metric-value">{interest}<span style="font-size:0.85rem;opacity:0.5">/10</span></div>', unsafe_allow_html=True)
            st.markdown(f'<div class="metric-label" style="margin-top:6px">暧昧指数</div><div class="metric-value">{ambiguity}<span style="font-size:0.85rem;opacity:0.5">%</span></div>', unsafe_allow_html=True)
            st.markdown('<div style="margin-top:10px;font-size:0.72rem;opacity:0.38">基于当前样本推断，非客观事实</div>', unsafe_allow_html=True)

            if scored and scored.get("interest_reasons"):
                with st.expander("评分依据", expanded=False):
                    for r in scored["interest_reasons"]:
                        st.markdown(f'<div style="font-size:0.82rem;opacity:0.7">· {r}</div>', unsafe_allow_html=True)
            st.markdown('</div>', unsafe_allow_html=True)

        # 行2：信号 + 推荐/风险
        col_c, col_d = st.columns(2)

        with col_c:
            st.markdown('<div class="apple-card">', unsafe_allow_html=True)
            st.markdown('<div style="font-size:0.7rem;font-weight:600;opacity:0.45;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:10px">信号识别</div>', unsafe_allow_html=True)

            ioi = result.get("ioi_signals", [])
            iod = result.get("iod_signals", [])
            if ioi:
                st.markdown('<div style="font-size:0.78rem;opacity:0.5;margin-bottom:4px">正向信号</div>', unsafe_allow_html=True)
                st.markdown(" ".join([f'<span class="tag tag-green">✓ {s}</span>' for s in ioi]), unsafe_allow_html=True)
            if iod:
                st.markdown('<div style="font-size:0.78rem;opacity:0.5;margin:8px 0 4px 0">风险信号</div>', unsafe_allow_html=True)
                st.markdown(" ".join([f'<span class="tag tag-orange">✗ {s}</span>' for s in iod]), unsafe_allow_html=True)
            if not ioi and not iod:
                st.markdown('<div style="opacity:0.35;font-size:0.85rem">暂无明显信号</div>', unsafe_allow_html=True)
            st.markdown('</div>', unsafe_allow_html=True)

        with col_d:
            st.markdown('<div class="apple-card">', unsafe_allow_html=True)
            st.markdown('<div style="font-size:0.7rem;font-weight:600;opacity:0.45;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:10px">行动建议</div>', unsafe_allow_html=True)

            opps = result.get("opportunities", [])
            flags = result.get("red_flags", [])
            rec = result.get("recommended_techniques", [])
            avoid = result.get("avoid_techniques", [])

            if opps:
                st.markdown('<div style="font-size:0.78rem;opacity:0.5;margin-bottom:4px">推荐方向</div>', unsafe_allow_html=True)
                for o in opps:
                    st.markdown(f'<div style="font-size:0.88rem;padding:2px 0;color:{colors["GREEN"]}">↗ {o}</div>', unsafe_allow_html=True)
            if rec:
                st.markdown('<div style="font-size:0.78rem;opacity:0.5;margin:8px 0 4px 0">适合技巧</div>', unsafe_allow_html=True)
                st.markdown(" ".join([f'<span class="tag">{t}</span>' for t in rec]), unsafe_allow_html=True)
            if flags or avoid:
                st.markdown('<div style="font-size:0.78rem;opacity:0.5;margin:8px 0 4px 0">不建议行为</div>', unsafe_allow_html=True)
                for f in (flags + avoid):
                    st.markdown(f'<div style="font-size:0.85rem;opacity:0.7;padding:2px 0">✗ {f}</div>', unsafe_allow_html=True)
            st.markdown('</div>', unsafe_allow_html=True)

    # ── 分析纠正 / 事实校正 ───────────────────────────────────────
    st.markdown('<div class="apple-card">', unsafe_allow_html=True)
    st.markdown('<div style="font-size:0.75rem;font-weight:600;opacity:0.5;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:8px">分析纠正 / 事实校正</div>', unsafe_allow_html=True)

    if not is_corrections_file_healthy():
        st.warning("纠正记录文件损坏，已忽略，重新保存后恢复正常")

    existing_corrections = get_corrections(name)
    if existing_corrections:
        st.markdown('<div style="font-size:0.78rem;opacity:0.5;margin-bottom:4px">当前已生效纠正：</div>', unsafe_allow_html=True)
        for c in existing_corrections:
            st.markdown(f'<div style="font-size:0.85rem;padding:2px 0;opacity:0.85">· {c["correction"]}</div>', unsafe_allow_html=True)
    else:
        st.markdown('<div style="font-size:0.82rem;opacity:0.35">暂无纠正记录</div>', unsafe_allow_html=True)

    correction_input = st.text_input(
        "correction_input",
        placeholder="例如：清吧喝酒那段不是邀约，只是在阐述当时发生的事，不要按邀约推进理解。",
        label_visibility="collapsed",
        key=f"correction_input_{name}",
    )

    col_c1, col_c2 = st.columns([2, 1])
    with col_c1:
        do_save_correction = st.button("保存纠正并重新分析", key=f"save_correction_{name}", use_container_width=True, type="primary")
    with col_c2:
        do_clear_correction = st.button("清空该联系人纠正", key=f"clear_correction_{name}", use_container_width=True)
    st.markdown('</div>', unsafe_allow_html=True)

    if do_save_correction:
        ok, msg = add_correction(name, correction_input)
        if not ok:
            st.warning(msg)
        else:
            st.success(msg)
            hint = get_correction_hint(name)
            from database import get_chat_history as _gch
            _last_text = st.session_state.get(f"last_chat_text_{name}", "")
            if _last_text.strip():
                _fmt, _msgs = auto_detect_and_parse(_last_text, name)
                _src_msgs = _msgs
            else:
                _src_msgs = _gch(name, limit=50)
            if _src_msgs:
                with st.spinner("结合纠正重新分析中..."):
                    result = analyze_conversation(name, _src_msgs, correction_hint=hint)
                    scored = score_from_analysis(result)
                if "error" in result:
                    st.error(result["error"])
                else:
                    st.session_state[f"last_analysis_{name}"] = result
                    st.session_state[f"last_scored_{name}"] = scored
            st.rerun()

    if do_clear_correction:
        ok, msg = clear_corrections(name)
        if ok:
            st.success(msg)
        else:
            st.info(msg)
        st.rerun()

    # ── 答疑导师 ──────────────────────────────────────────────────
    st.markdown('<div class="apple-card">', unsafe_allow_html=True)
    st.markdown('<div style="font-size:0.75rem;font-weight:600;opacity:0.5;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:4px">答疑导师</div>', unsafe_allow_html=True)
    st.caption("基于历史记录和分析结果答疑复盘，例如：我刚才那句话有没有问题 / 现在整体走向怎样 / 我有哪些失误")

    tutor_key = f"tutor_{name}"
    if tutor_key not in st.session_state:
        st.session_state[tutor_key] = []

    # 渲染对话历史
    for msg in st.session_state[tutor_key]:
        role = "user" if msg["role"] == "user" else "assistant"
        st.chat_message(role).write(msg["content"])

    col_ti, col_tb = st.columns([5, 1])
    with col_ti:
        tutor_input = st.text_input("tutor_q", placeholder="问问导师...",
                                    label_visibility="collapsed",
                                    value=st.session_state.get("tutor_input_val", ""),
                                    key="tutor_input")
        st.session_state["tutor_input_val"] = tutor_input
    with col_tb:
        do_tutor = st.button("发送", use_container_width=True, key="do_tutor")
    st.markdown('</div>', unsafe_allow_html=True)

    if do_tutor and tutor_input.strip():
        q = tutor_input.strip()
        st.session_state[tutor_key].append({"role": "user", "content": q})
        last_analysis = st.session_state.get(f"last_analysis_{name}")
        with st.spinner("思考中..."):
            reply = chat_tutor(
                name, q,
                last_analysis=last_analysis,
                tutor_history=st.session_state[tutor_key][:-1],
            )
        st.session_state[tutor_key].append({"role": "assistant", "content": reply})
        st.session_state["tutor_input_val"] = ""
        st.rerun()

# ─── Tab2: 回复建议 ───────────────────────────────────────────
with tab2:
    st.markdown('<div class="apple-card">', unsafe_allow_html=True)
    st.markdown("**她发来的消息**")
    her_msg = st.text_input("her_msg", placeholder="输入她最新那条消息...", label_visibility="collapsed")
    strategy_hint = st.text_input("strategy_hint", placeholder="策略方向（可选）：例如今天想约出来 / 保持神秘感", label_visibility="collapsed")

    col_q, col_g = st.columns([1, 1])
    with col_q:
        do_quick = st.button("快速解读", use_container_width=True)
    with col_g:
        do_reply = st.button("生成回复建议", type="primary", use_container_width=True)
    st.markdown('</div>', unsafe_allow_html=True)

    if do_quick and her_msg.strip():
        with st.spinner("解读中..."):
            quick = quick_analyze_message(name, her_msg)
        st.session_state[f"quick_{name}"] = quick

    elif do_quick and not her_msg.strip():
        from database import get_chat_history as _gch2
        _recent = [m for m in _gch2(name, limit=30) if m["role"] == "her"]
        if not _recent:
            st.warning("暂无她的历史消息，请先导入聊天记录")
        else:
            _last_her = _recent[-1]["content"]
            st.caption(f"输入框为空，基于最新一条消息解读：「{_last_her}」")
            with st.spinner("解读中..."):
                quick = quick_analyze_message(name, _last_her)
            st.session_state[f"quick_{name}"] = quick

    if do_reply and her_msg.strip():
        with st.spinner("生成回复中..."):
            reply_result = generate_reply(name, her_msg, strategy_hint)
        st.session_state[f"reply_{name}"] = reply_result

    elif do_reply and not her_msg.strip():
        from database import get_chat_history as _gch3
        _recent2 = [m for m in _gch3(name, limit=30) if m["role"] == "her"]
        if not _recent2:
            st.warning("暂无她的历史消息，请先导入聊天记录")
        else:
            _last_her2 = _recent2[-1]["content"]
            st.caption(f"输入框为空，基于最新一条消息生成回复：「{_last_her2}」")
            with st.spinner("生成回复中..."):
                reply_result = generate_reply(name, _last_her2, strategy_hint)
            st.session_state[f"reply_{name}"] = reply_result

    quick = st.session_state.get(f"quick_{name}")
    if quick:
        st.markdown('<div class="apple-card">', unsafe_allow_html=True)
        st.markdown('<div style="font-size:0.7rem;font-weight:600;opacity:0.45;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:8px">快速解读 <span style="font-weight:400;font-size:0.68rem;opacity:0.6">· AI 推断</span></div>', unsafe_allow_html=True)
        take = quick.get("quick_take", "")
        sentiment = quick.get("sentiment", "")
        ioi = "、".join(quick.get("ioi_signals", [])) or "无"
        iod = "、".join(quick.get("iod_signals", [])) or "无"
        st.markdown(f'<div style="font-size:1rem;font-weight:600;margin-bottom:6px">{take}</div>', unsafe_allow_html=True)
        st.markdown(f'<div style="font-size:0.85rem;opacity:0.6">情绪：{sentiment} &nbsp;·&nbsp; 积极：{ioi} &nbsp;·&nbsp; 消极：{iod}</div>', unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)

    reply_result = st.session_state.get(f"reply_{name}")
    if reply_result:
        if reply_result.get("analysis"):
            st.markdown('<div class="apple-card">', unsafe_allow_html=True)
            st.markdown('<div style="font-size:0.7rem;font-weight:600;opacity:0.45;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:8px">当前解读</div>', unsafe_allow_html=True)
            st.markdown(f'<div style="font-size:0.9rem;line-height:1.6">{reply_result["analysis"]}</div>', unsafe_allow_html=True)
            if reply_result.get("recommended_direction"):
                st.markdown(f'<div style="margin-top:8px;font-size:0.85rem;opacity:0.6">推荐方向 → {reply_result["recommended_direction"]}</div>', unsafe_allow_html=True)
            st.markdown('</div>', unsafe_allow_html=True)

        risk_colors = {"low": colors["GREEN"], "medium": colors["ORANGE"], "high": "#FF3B30"}
        for opt in reply_result.get("reply_options", []):
            risk_color = risk_colors.get(opt.get("risk_level", "low"), colors["GREEN"])
            risk_label = {"low": "低风险", "medium": "中风险", "high": "高风险"}.get(opt.get("risk_level", "low"), "低风险")
            # 兼容新结构（main_reply/optional_followup/reason）和旧结构（content/fit_reason）
            main_reply = opt.get("main_reply") or opt.get("content", "")
            followup = opt.get("optional_followup", "")
            reason = opt.get("reason") or opt.get("fit_reason", "")
            display_text = main_reply + (f"<br><span style='opacity:0.55'>+ {followup}</span>" if followup else "")
            copy_text = main_reply + (f"\n{followup}" if followup else "")
            st.markdown(f"""<div class="apple-card" style="margin-bottom:0">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">
    <div style="font-size:0.8rem;font-weight:700;opacity:0.7">{opt['label']}</div>
    <span style="font-size:0.72rem;color:{risk_color}">{risk_label}</span>
  </div>
  <div style="font-size:0.95rem;font-weight:500;margin-bottom:6px">{display_text}</div>
  <div style="font-size:0.78rem;opacity:0.5">↗ {reason}</div>
</div>""", unsafe_allow_html=True)
            st.code(copy_text, language=None)

        if reply_result.get("avoid"):
            st.markdown('<div class="apple-card">', unsafe_allow_html=True)
            st.markdown('<div style="font-size:0.7rem;font-weight:600;opacity:0.45;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:8px">不建议发送</div>', unsafe_allow_html=True)
            for a in reply_result["avoid"]:
                st.markdown(f'<span class="tag tag-orange">✗ {a}</span>', unsafe_allow_html=True)
            st.markdown('</div>', unsafe_allow_html=True)

    # ── 我想发的内容 · 评估模块 ──────────────────────────────────
    st.markdown('<div class="apple-card">', unsafe_allow_html=True)
    st.markdown('<div style="font-size:0.75rem;font-weight:600;opacity:0.5;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:8px">我想发的内容 · 评估</div>', unsafe_allow_html=True)
    st.caption("输入你想发的回复，AI 评估是否合适。不计入聊天记录。")
    my_draft = st.text_input("my_draft", placeholder="输入你想发的那句话...", label_visibility="collapsed", key="my_draft_input")
    do_eval = st.button("评估这条回复", use_container_width=True, key="do_eval")
    st.markdown('</div>', unsafe_allow_html=True)

    if do_eval and my_draft.strip():
        if not her_msg.strip():
            from database import get_chat_history as _gch4
            _recent3 = [m for m in _gch4(name, limit=30) if m["role"] == "her"]
            if not _recent3:
                st.warning("请先在上方填入她发来的消息，或先导入聊天记录")
            else:
                _eval_her = _recent3[-1]["content"]
                st.caption(f"输入框为空，基于最新一条消息评估：「{_eval_her}」")
                with st.spinner("评估中..."):
                    eval_result = evaluate_my_reply(name, _eval_her, my_draft)
                st.session_state[f"eval_{name}"] = eval_result
        else:
            with st.spinner("评估中..."):
                eval_result = evaluate_my_reply(name, her_msg, my_draft)
            st.session_state[f"eval_{name}"] = eval_result

    eval_result = st.session_state.get(f"eval_{name}")
    if eval_result and eval_result.get("verdict") != "解析失败":
        verdict = eval_result.get("verdict", "")
        score = eval_result.get("score", 0)
        verdict_color = {
            "合适": colors["GREEN"],
            "勉强": colors["ORANGE"],
            "不建议": "#FF3B30",
        }.get(verdict, colors["TEXT_SEC"])

        st.markdown('<div class="apple-card">', unsafe_allow_html=True)
        st.markdown(
            f'<div style="display:flex;align-items:center;gap:12px;margin-bottom:10px">'
            f'<div style="font-size:1.1rem;font-weight:700;color:{verdict_color}">{verdict}</div>'
            f'<div style="font-size:0.85rem;opacity:0.5">{score}/10</div>'
            f'</div>',
            unsafe_allow_html=True
        )
        if eval_result.get("strengths"):
            st.markdown('<div style="font-size:0.75rem;opacity:0.5;margin-bottom:4px">符合的点</div>', unsafe_allow_html=True)
            for s in eval_result["strengths"]:
                st.markdown(f'<div style="font-size:0.85rem;padding:2px 0;color:{colors["GREEN"]}">✓ {s}</div>', unsafe_allow_html=True)
        if eval_result.get("risks"):
            st.markdown('<div style="font-size:0.75rem;opacity:0.5;margin:8px 0 4px 0">风险点</div>', unsafe_allow_html=True)
            for r in eval_result["risks"]:
                st.markdown(f'<div style="font-size:0.85rem;padding:2px 0;color:{colors["ORANGE"]}">✗ {r}</div>', unsafe_allow_html=True)
        if eval_result.get("suggestion"):
            st.markdown('<div style="font-size:0.75rem;opacity:0.5;margin:8px 0 4px 0">调整方向</div>', unsafe_allow_html=True)
            st.markdown(f'<div style="font-size:0.88rem;line-height:1.6;opacity:0.85">{eval_result["suggestion"]}</div>', unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)


with tab3:
    st.markdown('<div class="apple-card">', unsafe_allow_html=True)
    st.markdown("**开场白风格**")
    opening_style = st.radio(
        "style",
        ["轻松自然", "有趣调侃", "低压破冰", "重新联系"],
        horizontal=True,
        label_visibility="collapsed"
    )
    context_o = st.text_area("背景信息（可选）", height=72,
                             placeholder="例如：健身房认识的 / 朋友介绍 / 她是做设计的 / 三天没联系了",
                             label_visibility="collapsed")
    if st.button("生成开场白", type="primary"):
        hint = f"风格：{opening_style}。{context_o}"
        with st.spinner("创意生成中..."):
            opening_result = generate_opening(name, hint)
        st.session_state[f"opening_{name}"] = opening_result
    st.markdown('</div>', unsafe_allow_html=True)

    opening_result = st.session_state.get(f"opening_{name}")
    if opening_result:
        if opening_result.get("analysis"):
            st.markdown('<div class="apple-card">', unsafe_allow_html=True)
            st.markdown('<div style="font-size:0.7rem;font-weight:600;opacity:0.45;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:6px">当前适合风格</div>', unsafe_allow_html=True)
            st.markdown(f'<div style="font-size:0.9rem;opacity:0.75">{opening_result["analysis"]}</div>', unsafe_allow_html=True)
            st.markdown('</div>', unsafe_allow_html=True)

        risk_colors = {"low": colors["GREEN"], "medium": colors["ORANGE"], "high": "#FF3B30"}
        for opt in opening_result.get("reply_options", []):
            risk_color = risk_colors.get(opt.get("risk_level", "low"), colors["GREEN"])
            risk_label = {"low": "低风险", "medium": "中风险", "high": "高风险"}.get(opt.get("risk_level", "low"), "低风险")
            st.markdown(f"""<div class="apple-card" style="margin-bottom:0">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">
    <div style="font-size:0.8rem;font-weight:700;opacity:0.7">{opt['label']}</div>
    <span style="font-size:0.72rem;color:{risk_color}">{risk_label}</span>
  </div>
  <div style="font-size:0.95rem;font-weight:500;margin-bottom:6px">{opt['content']}</div>
  <div style="font-size:0.78rem;opacity:0.5">↗ {opt.get('fit_reason','')}</div>
</div>""", unsafe_allow_html=True)
            st.code(opt["content"], language=None)

        if opening_result.get("avoid"):
            st.markdown('<div class="apple-card">', unsafe_allow_html=True)
            st.markdown('<div style="font-size:0.7rem;font-weight:600;opacity:0.45;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:8px">不建议的开场方式</div>', unsafe_allow_html=True)
            for a in opening_result["avoid"]:
                st.markdown(f'<span class="tag tag-orange">✗ {a}</span>', unsafe_allow_html=True)
            st.markdown('</div>', unsafe_allow_html=True)

# ─── Tab4: 策略中心 ───────────────────────────────────────────
with tab4:
    col_left, col_right = st.columns([3, 2])

    with col_left:
        st.markdown('<div class="apple-card">', unsafe_allow_html=True)
        st.markdown("**当前背景（可选）**")
        context_s = st.text_area("context_s", height=72,
                                 placeholder="例如：刚冷战两天 / 她今天主动发消息了 / 想约出来",
                                 label_visibility="collapsed")
        if st.button("生成策略方案", type="primary"):
            with st.spinner("策略分析中..."):
                strategy = generate_strategy(name, context_s)
            st.session_state[f"strategy_{name}"] = strategy
        st.markdown('</div>', unsafe_allow_html=True)

        strategy = st.session_state.get(f"strategy_{name}")
        if strategy and "error" not in strategy:
            st.markdown('<div class="apple-card">', unsafe_allow_html=True)
            st.markdown('<div style="font-size:0.7rem;font-weight:600;opacity:0.45;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:8px">当前局面</div>', unsafe_allow_html=True)
            st.markdown(f'<div style="font-size:0.92rem;line-height:1.6">{strategy.get("current_situation_assessment","")}</div>', unsafe_allow_html=True)
            st.markdown('</div>', unsafe_allow_html=True)

            st.markdown('<div class="apple-card">', unsafe_allow_html=True)
            st.markdown('<div style="font-size:0.7rem;font-weight:600;opacity:0.45;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:8px">关键一招</div>', unsafe_allow_html=True)
            st.markdown(f'<div style="font-size:0.95rem;font-weight:600;line-height:1.6;color:{colors["BLUE"]}">{strategy.get("key_move","")}</div>', unsafe_allow_html=True)
            if strategy.get("pacing_advice"):
                st.markdown(f'<div style="font-size:0.82rem;opacity:0.55;margin-top:6px">节奏 · {strategy["pacing_advice"]}</div>', unsafe_allow_html=True)
            if strategy.get("next_milestone"):
                st.markdown(f'<div style="font-size:0.82rem;opacity:0.55;margin-top:4px">目标 · {strategy["next_milestone"]}</div>', unsafe_allow_html=True)
            st.markdown('</div>', unsafe_allow_html=True)

            if strategy.get("risk_warning"):
                st.markdown('<div class="apple-card">', unsafe_allow_html=True)
                st.markdown('<div style="font-size:0.7rem;font-weight:600;opacity:0.45;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:8px">风险提醒</div>', unsafe_allow_html=True)
                st.markdown(f'<div style="font-size:0.88rem;opacity:0.8">{strategy["risk_warning"]}</div>', unsafe_allow_html=True)
                st.markdown('</div>', unsafe_allow_html=True)

    with col_right:
        strategy = st.session_state.get(f"strategy_{name}")
        if strategy and "error" not in strategy:
            st.markdown('<div class="apple-card">', unsafe_allow_html=True)
            st.markdown('<div style="font-size:0.7rem;font-weight:600;opacity:0.45;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:10px">当前适合技巧</div>', unsafe_allow_html=True)
            for tech in strategy.get("priority_techniques", []):
                with st.expander(f"✦ {tech.get('technique','')}"):
                    st.markdown(f'<div style="font-size:0.85rem;margin-bottom:4px"><strong>为什么：</strong>{tech.get("why","")}</div>', unsafe_allow_html=True)
                    st.markdown(f'<div style="font-size:0.85rem;margin-bottom:4px"><strong>怎么做：</strong>{tech.get("how","")}</div>', unsafe_allow_html=True)
                    if tech.get("condition"):
                        st.markdown(f'<div style="font-size:0.8rem;opacity:0.55">适用条件：{tech["condition"]}</div>', unsafe_allow_html=True)
            st.markdown('</div>', unsafe_allow_html=True)

            if strategy.get("avoid_techniques"):
                st.markdown('<div class="apple-card">', unsafe_allow_html=True)
                st.markdown('<div style="font-size:0.7rem;font-weight:600;opacity:0.45;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:8px">暂时避开</div>', unsafe_allow_html=True)
                for t in strategy["avoid_techniques"]:
                    # 兼容新结构（dict）和旧结构（str）
                    if isinstance(t, dict):
                        tech_name = t.get("technique", "")
                        tech_reason = t.get("reason", "")
                        st.markdown(f'<div style="font-size:0.85rem;padding:3px 0"><span class="tag">{tech_name}</span> <span style="opacity:0.6;font-size:0.8rem">{tech_reason}</span></div>', unsafe_allow_html=True)
                    else:
                        st.markdown(f'<span class="tag">{t}</span>', unsafe_allow_html=True)
                st.markdown('</div>', unsafe_allow_html=True)

        st.markdown('<div class="apple-card">', unsafe_allow_html=True)
        st.markdown('<div style="font-size:0.7rem;font-weight:600;opacity:0.45;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:6px">技巧详解</div>', unsafe_allow_html=True)
        st.caption("选择任意技巧，获取针对当前联系人的专属使用指南")
        selected_tech = st.selectbox("选择技巧", GAME_TECHNIQUES, label_visibility="collapsed")
        if st.button("获取使用指南", type="primary", use_container_width=True):
            with st.spinner("生成指南..."):
                guide = get_technique_guide(selected_tech, name)
            st.session_state[f"guide_{name}_{selected_tech}"] = guide

        guide = st.session_state.get(f"guide_{name}_{selected_tech}")
        if guide:
            st.markdown(guide)
        st.markdown('</div>', unsafe_allow_html=True)
