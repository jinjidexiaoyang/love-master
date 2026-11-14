"""
pages/contacts.py — 联系人详情页
"""
import streamlit as st
import plotly.graph_objects as go
import pandas as pd
import json
import os

from database import (
    init_db, add_contact, get_contact, get_all_contacts,
    get_metrics_history, get_advisor_history, delete_contact,
    get_daily_message_counts, get_chat_history
)
from utils.daily_scoring import rebuild_daily_metrics
from utils.language_features import compute_chat_stats
from agents.replier import advisor_chat
from config import RELATIONSHIP_STAGES
from utils.ui_helpers import apply_theme, page_header

@st.cache_resource
def setup_db():
    init_db()
setup_db()

colors = apply_theme()

with st.sidebar:
    with st.expander("＋ 添加联系人"):
        new_name = st.text_input("姓名", key="new_name", placeholder="备注名或真实姓名")
        new_nick = st.text_input("昵称（可选）", key="new_nick")
        if st.button("添加", use_container_width=True, type="primary"):
            if new_name.strip():
                success = add_contact(new_name.strip(), new_nick.strip())
                if success:
                    st.success(f"已添加 {new_name}")
                    st.rerun()
                else:
                    st.error("该联系人已存在")

    contacts = get_all_contacts()
    if contacts:
        names = [c["name"] for c in contacts]
        if "selected_name" not in st.session_state or st.session_state.selected_name not in names:
            st.session_state.selected_name = names[0]
        current = st.session_state.selected_name
        with st.expander(f"👤 {current}", expanded=False):
            for name in names:
                is_active = name == current
                if st.button(name, key=f"c_{name}", use_container_width=True,
                             type="primary" if is_active else "secondary"):
                    st.session_state.selected_name = name
                    st.rerun()

        st.markdown("<hr style='border:none;border-top:1px solid rgba(128,128,128,0.2);margin:12px 0'>", unsafe_allow_html=True)
        with st.expander("🗑 删除联系人"):
            del_name = st.selectbox("选择要删除的联系人", names, key="del_select")
            st.warning(f"删除「{del_name}」将清除其所有聊天记录、分析数据和顾问对话，不可恢复。")
            confirm = st.checkbox(f"我确认要删除「{del_name}」", key="del_confirm")
            if st.button("确认删除", type="primary", use_container_width=True, disabled=not confirm):
                delete_contact(del_name)
                if st.session_state.get("selected_name") == del_name:
                    st.session_state.pop("selected_name", None)
                st.session_state.pop("del_confirm", None)
                st.rerun()

# ─── 主内容 ───────────────────────────────────────────────
contacts = get_all_contacts()
if not contacts:
    st.markdown("""<div style="text-align:center;padding:80px 0;">
      <div style="font-size:1.5rem;font-weight:700;margin-bottom:8px;">还没有联系人</div>
      <div style="opacity:0.5;">在左侧添加第一个联系人</div>
    </div>""", unsafe_allow_html=True)
    st.stop()

name = st.session_state.get("selected_name", contacts[0]["name"])
contact = get_contact(name)
if not contact:
    st.stop()

stage = contact.get("stage", "陌生人")
interest = contact.get("interest_score") or 0.0
ambiguity = contact.get("ambiguity_percent") or 0
momentum = contact.get("momentum") or "stable"
stage_idx = RELATIONSHIP_STAGES.index(stage) if stage in RELATIONSHIP_STAGES else 0
progress_pct = round(stage_idx / (len(RELATIONSHIP_STAGES) - 1) * 100)

page_header(name, f"联系人详情 · {stage}")

# 核心指标
momentum_label = {"rising": "↑ 上升", "stable": "→ 稳定", "falling": "↓ 下降"}.get(momentum, "→ 稳定")
momentum_color = {"rising": colors["GREEN"], "stable": colors["TEXT_SEC"], "falling": colors["ORANGE"]}.get(momentum, colors["TEXT_SEC"])

st.markdown('<div class="apple-card">', unsafe_allow_html=True)
c1, c2, c3, c4 = st.columns(4)
with c1:
    st.markdown(f'<div class="metric-label">关系阶段</div><div class="metric-value" style="font-size:1.4rem">{stage}</div>', unsafe_allow_html=True)
with c2:
    st.markdown(f'<div class="metric-label">好感度</div><div class="metric-value">{interest}<span style="font-size:1rem;opacity:0.5">/10</span></div>', unsafe_allow_html=True)
with c3:
    st.markdown(f'<div class="metric-label">暧昧指数</div><div class="metric-value">{ambiguity}<span style="font-size:1rem;opacity:0.5">%</span></div>', unsafe_allow_html=True)
with c4:
    st.markdown(f'<div class="metric-label">势头</div><div class="metric-value" style="font-size:1.4rem;color:{momentum_color}">{momentum_label}</div>', unsafe_allow_html=True)
st.markdown('</div>', unsafe_allow_html=True)

# 阶段进度条
st.markdown('<div class="apple-card">', unsafe_allow_html=True)
st.markdown('<div style="font-size:0.75rem;font-weight:600;opacity:0.5;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:12px">关系进展</div>', unsafe_allow_html=True)
stage_cols = st.columns(len(RELATIONSHIP_STAGES))
for i, s in enumerate(RELATIONSHIP_STAGES):
    with stage_cols[i]:
        if s == stage:
            st.markdown(f'<div class="stage-item stage-active">{s}</div>', unsafe_allow_html=True)
        elif i < stage_idx:
            st.markdown(f'<div class="stage-item stage-done">✓ {s}</div>', unsafe_allow_html=True)
        else:
            st.markdown(f'<div class="stage-item stage-future">{s}</div>', unsafe_allow_html=True)
st.markdown('</div>', unsafe_allow_html=True)

# 曲线图
PLOT_BG = colors["PLOT_BG"]
GRID = colors["GRID"]
PLOT_PAPER = colors["PLOT_PAPER"]

daily = get_daily_message_counts(name)

# 如果 metrics_history 为空但有聊天记录，自动按天计算
metrics = get_metrics_history(name)
if not metrics and get_chat_history(name, limit=1):
    rebuild_daily_metrics(name)
    metrics = get_metrics_history(name)

col_c1, col_c2 = st.columns(2)

with col_c1:
    st.markdown('<div class="apple-card">', unsafe_allow_html=True)
    st.markdown('<div style="font-size:0.75rem;font-weight:600;opacity:0.5;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:8px">每日消息量</div>', unsafe_allow_html=True)
    if daily:
        df_d = pd.DataFrame(daily)
        df_d["date"] = pd.to_datetime(df_d["date"], errors="coerce").dt.date
        df_d = df_d.dropna(subset=["date"])
        if df_d.empty:
            st.markdown('<div style="opacity:0.4;font-size:0.85rem;padding:32px 0;text-align:center">暂无可用日期数据</div>', unsafe_allow_html=True)
        else:
            min_dd, max_dd = df_d["date"].min(), df_d["date"].max()
            if min_dd < max_dd:
                sel_d = st.slider("", min_value=min_dd, max_value=max_dd,
                                  value=(min_dd, max_dd), format="MM/DD",
                                  label_visibility="collapsed", key="daily_slider")
                df_d = df_d[df_d["date"].between(sel_d[0], sel_d[1])]
            fig = go.Figure()
            fig.add_trace(go.Bar(
                x=df_d["date"], y=df_d["her"],
                name="她", marker_color=colors["GREEN"], opacity=0.85))
            fig.add_trace(go.Bar(
                x=df_d["date"], y=df_d["me"],
                name="我", marker_color=colors["BLUE"], opacity=0.85))
            fig.update_layout(
                barmode="stack",
                xaxis=dict(showgrid=False, tickangle=-45, tickfont=dict(size=10)),
                yaxis=dict(showgrid=True, gridcolor=GRID),
                plot_bgcolor=PLOT_BG, paper_bgcolor=PLOT_PAPER,
                height=200, margin=dict(l=0, r=0, t=0, b=40),
                legend=dict(orientation="h", y=1.1, x=0, font=dict(size=11)))
            st.plotly_chart(fig, use_container_width=True)
    else:
        st.markdown('<div style="opacity:0.4;font-size:0.85rem;padding:32px 0;text-align:center">暂无消息数据</div>', unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)

with col_c2:
    st.markdown('<div class="apple-card">', unsafe_allow_html=True)
    st.markdown('<div style="font-size:0.75rem;font-weight:600;opacity:0.5;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:8px">好感度 · 暧昧度趋势</div>', unsafe_allow_html=True)
    if metrics:
        df_m = pd.DataFrame(metrics)
        df_m["date"] = pd.to_datetime(df_m["timestamp"], errors="coerce").dt.date
        df_m = df_m.dropna(subset=["date"])
        if df_m.empty:
            st.markdown('<div style="opacity:0.4;font-size:0.85rem;padding:32px 0;text-align:center">暂无可用日期数据</div>', unsafe_allow_html=True)
        else:
            min_d, max_d = df_m["date"].min(), df_m["date"].max()
            if min_d < max_d:
                sel = st.slider("", min_value=min_d, max_value=max_d,
                                value=(min_d, max_d), format="MM/DD",
                                label_visibility="collapsed", key="metrics_slider")
                df_m = df_m[df_m["date"].between(sel[0], sel[1])]
            fig2 = go.Figure()
            fig2.add_trace(go.Scatter(
                x=df_m["date"], y=df_m["interest_score"],
                name="好感度", mode="lines+markers",
                line=dict(color=colors["BLUE"], width=2.5),
                marker=dict(size=6), yaxis="y1"))
            fig2.add_trace(go.Scatter(
                x=df_m["date"], y=df_m["ambiguity_percent"],
                name="暧昧度%", mode="lines+markers",
                line=dict(color=colors["GREEN"], width=2.5, dash="dot"),
                marker=dict(size=6), yaxis="y2"))
            fig2.update_layout(
                yaxis=dict(range=[0, 10], showgrid=True, gridcolor=GRID),
                yaxis2=dict(range=[0, 100], overlaying="y", side="right", showgrid=False),
                xaxis=dict(showgrid=False),
                plot_bgcolor=PLOT_BG, paper_bgcolor=PLOT_PAPER,
                height=200, margin=dict(l=0, r=30, t=0, b=0),
                legend=dict(orientation="h", y=1.15, x=0, font=dict(size=11)))
            st.plotly_chart(fig2, use_container_width=True)
    else:
        st.markdown('<div style="opacity:0.4;font-size:0.85rem;padding:32px 0;text-align:center">暂无数据</div>', unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)


# 画像标签
col_l, col_r = st.columns(2)
with col_l:
    st.markdown('<div class="apple-card">', unsafe_allow_html=True)
    st.markdown('<div style="font-size:0.75rem;font-weight:600;opacity:0.5;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:12px">性格标签</div>', unsafe_allow_html=True)
    try:
        tags = json.loads(contact.get("personality_tags", "[]"))
    except Exception:
        tags = []
    if tags:
        st.markdown(" ".join([f'<span class="tag">{t}</span>' for t in tags]), unsafe_allow_html=True)
    else:
        st.markdown('<div style="opacity:0.4;font-size:0.85rem">分析对话后自动生成</div>', unsafe_allow_html=True)

    st.markdown('<div style="font-size:0.75rem;font-weight:600;opacity:0.5;text-transform:uppercase;letter-spacing:0.5px;margin:16px 0 8px 0">沟通风格</div>', unsafe_allow_html=True)
    comm = contact.get("communication_style", "")
    comm_html = comm if comm else '<span style="opacity:0.4">暂无</span>'
    st.markdown(f'<div style="font-size:0.9rem;line-height:1.5">{comm_html}</div>', unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)

with col_r:
    st.markdown('<div class="apple-card">', unsafe_allow_html=True)
    st.markdown('<div style="font-size:0.75rem;font-weight:600;opacity:0.5;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:12px">兴趣爱好</div>', unsafe_allow_html=True)
    try:
        interests = json.loads(contact.get("interests", "[]"))
    except Exception:
        interests = []
    if interests:
        for item in interests:
            st.markdown(f'<div style="font-size:0.9rem;padding:3px 0">· {item}</div>', unsafe_allow_html=True)
    else:
        st.markdown('<div style="opacity:0.4;font-size:0.85rem">暂无信息</div>', unsafe_allow_html=True)

    if contact.get("mbti"):
        st.markdown(f'<div style="margin-top:12px"><span class="tag tag-blue">MBTI: {contact["mbti"]}</span></div>', unsafe_allow_html=True)

    if contact.get("last_analysis"):
        st.markdown('<div style="font-size:0.75rem;font-weight:600;opacity:0.5;text-transform:uppercase;letter-spacing:0.5px;margin:16px 0 8px 0">最近分析</div>', unsafe_allow_html=True)
        st.markdown(f'<div style="font-size:0.88rem;opacity:0.8;line-height:1.5">{contact["last_analysis"]}</div>', unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)

# ── 互动统计 ────────────────────────────────────────────────
with st.expander("📊 聊天节奏 · 互动统计", expanded=False):
    _cs = compute_chat_stats(name)
    if _cs is None:
        st.caption("统计数据读取失败，请检查数据库连接。")
    elif not _cs.get("sufficient"):
        _t = _cs.get("total_her", 0) + _cs.get("total_me", 0)
        st.caption(f"样本不足（当前共 {_t} 条消息，至少需要 20 条）。导入更多聊天记录后重新查看。")
    else:
        # ── 消息占比 ──────────────────────────────────────────
        _total = _cs["total_her"] + _cs["total_me"]
        _her_pct = round(_cs["total_her"] / _total * 100)
        _me_pct  = 100 - _her_pct

        st.markdown(
            '<div style="font-size:0.75rem;font-weight:600;opacity:0.5;'
            'text-transform:uppercase;letter-spacing:0.5px;margin-bottom:10px">'
            '消息占比</div>',
            unsafe_allow_html=True,
        )
        _col_h, _col_m = st.columns(2)
        with _col_h:
            st.metric("对方发送", f"{_cs['total_her']} 条", f"{_her_pct}%")
        with _col_m:
            st.metric("我发送", f"{_cs['total_me']} 条", f"{_me_pct}%")

        st.markdown("<hr style='border:none;border-top:1px solid rgba(128,128,128,0.15);margin:12px 0'>", unsafe_allow_html=True)

        # ── 平均长度 + 问句/情绪比例 ──────────────────────────
        st.markdown(
            '<div style="font-size:0.75rem;font-weight:600;opacity:0.5;'
            'text-transform:uppercase;letter-spacing:0.5px;margin-bottom:10px">'
            '语言特征（基于对方消息）</div>',
            unsafe_allow_html=True,
        )
        _fc1, _fc2, _fc3, _fc4 = st.columns(4)
        with _fc1:
            st.metric("对方均长", f"{_cs['avg_len_her']} 字")
        with _fc2:
            st.metric("我均长", f"{_cs['avg_len_me']} 字")
        with _fc3:
            st.metric("对方问句率", f"{_cs['question_rate_her']}%")
        with _fc4:
            _pos = _cs['positive_rate_her']
            _neg = _cs['negative_rate_her']
            st.metric("正/负情绪词", f"{_pos}% / {_neg}%")

        st.markdown("<hr style='border:none;border-top:1px solid rgba(128,128,128,0.15);margin:12px 0'>", unsafe_allow_html=True)

        # ── 24 小时活跃时段 ───────────────────────────────────
        st.markdown(
            '<div style="font-size:0.75rem;font-weight:600;opacity:0.5;'
            'text-transform:uppercase;letter-spacing:0.5px;margin-bottom:8px">'
            '24 小时活跃时段</div>',
            unsafe_allow_html=True,
        )
        _df_h = pd.DataFrame({
            "小时": list(range(24)),
            "对方": _cs["hourly_her"],
            "我":   _cs["hourly_me"],
        })
        _fig_h = go.Figure()
        _fig_h.add_trace(go.Bar(
            x=_df_h["小时"], y=_df_h["对方"],
            name="对方", marker_color=colors["GREEN"], opacity=0.8,
        ))
        _fig_h.add_trace(go.Bar(
            x=_df_h["小时"], y=_df_h["我"],
            name="我", marker_color=colors["BLUE"], opacity=0.8,
        ))
        _fig_h.update_layout(
            barmode="stack",
            xaxis=dict(
                tickvals=list(range(24)),
                ticktext=[f"{h}时" if h % 6 == 0 else "" for h in range(24)],
                showgrid=False,
            ),
            yaxis=dict(showgrid=True, gridcolor=colors["GRID"]),
            plot_bgcolor=colors["PLOT_BG"],
            paper_bgcolor=colors["PLOT_PAPER"],
            height=180,
            margin=dict(l=0, r=0, t=0, b=30),
            legend=dict(orientation="h", y=1.1, x=0, font=dict(size=11)),
        )
        st.plotly_chart(_fig_h, use_container_width=True)

        st.markdown("<hr style='border:none;border-top:1px solid rgba(128,128,128,0.15);margin:12px 0'>", unsafe_allow_html=True)

        # ── 高频词 Top 10 ─────────────────────────────────────
        st.markdown(
            '<div style="font-size:0.75rem;font-weight:600;opacity:0.5;'
            'text-transform:uppercase;letter-spacing:0.5px;margin-bottom:8px">'
            '高频词 Top 10</div>',
            unsafe_allow_html=True,
        )
        _wc1, _wc2 = st.columns(2)
        with _wc1:
            st.markdown('<div style="font-size:0.82rem;opacity:0.6;margin-bottom:6px">对方</div>', unsafe_allow_html=True)
            if _cs["top_words_her"]:
                _max_c = _cs["top_words_her"][0][1] or 1
                for _w, _c in _cs["top_words_her"]:
                    _bar_w = int(_c / _max_c * 100)
                    st.markdown(
                        f'<div style="display:flex;align-items:center;gap:8px;margin:3px 0">'
                        f'<div style="width:52px;font-size:0.85rem;font-weight:600">{_w}</div>'
                        f'<div style="flex:1;background:rgba(128,128,128,0.12);border-radius:3px;height:10px">'
                        f'<div style="width:{_bar_w}%;background:{colors["GREEN"]};height:100%;border-radius:3px;opacity:0.7"></div>'
                        f'</div>'
                        f'<div style="width:28px;text-align:right;font-size:0.78rem;opacity:0.5">{_c}</div>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )
            else:
                st.caption("词频数据不足")
        with _wc2:
            st.markdown('<div style="font-size:0.82rem;opacity:0.6;margin-bottom:6px">我</div>', unsafe_allow_html=True)
            if _cs["top_words_me"]:
                _max_cm = _cs["top_words_me"][0][1] or 1
                for _w, _c in _cs["top_words_me"]:
                    _bar_w = int(_c / _max_cm * 100)
                    st.markdown(
                        f'<div style="display:flex;align-items:center;gap:8px;margin:3px 0">'
                        f'<div style="width:52px;font-size:0.85rem;font-weight:600">{_w}</div>'
                        f'<div style="flex:1;background:rgba(128,128,128,0.12);border-radius:3px;height:10px">'
                        f'<div style="width:{_bar_w}%;background:{colors["BLUE"]};height:100%;border-radius:3px;opacity:0.7"></div>'
                        f'</div>'
                        f'<div style="width:28px;text-align:right;font-size:0.78rem;opacity:0.5">{_c}</div>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )
            else:
                st.caption("词频数据不足")

        st.markdown(
            '<div style="font-size:0.75rem;opacity:0.35;margin-top:12px">'
            '以上为规则统计辅助参考，不替代 AI 分析判断。</div>',
            unsafe_allow_html=True,
        )

# AI 顾问
st.markdown('<div class="apple-card">', unsafe_allow_html=True)
st.markdown('<div style="font-size:0.75rem;font-weight:600;opacity:0.5;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:16px">AI 恋爱顾问</div>', unsafe_allow_html=True)
advisor_history = get_advisor_history(name)
for msg in advisor_history[-10:]:
    role = "user" if msg["role"] == "user" else "assistant"
    st.chat_message(role).write(msg["content"])
st.markdown('</div>', unsafe_allow_html=True)

advisor_input = st.chat_input(f"问问 AI 关于 {name} 的任何问题...")
if advisor_input:
    with st.spinner("思考中..."):
        advisor_chat(name, advisor_input)
    st.rerun()
