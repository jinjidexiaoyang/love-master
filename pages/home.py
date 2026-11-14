"""
pages/home.py — 总览 Dashboard
"""
import plotly.graph_objects as go
import streamlit as st

from config import RELATIONSHIP_STAGES
from database import init_db, get_all_contacts
from utils.ui_helpers import apply_theme, page_header, section_label

@st.cache_resource
def setup_db():
    init_db()
setup_db()

colors = apply_theme()

page_header("总览", "所有联系人关系状态一览")

contacts = get_all_contacts()

if not contacts:
    st.markdown("""<div class="hero">
      <div class="hero-icon">✦</div>
      <div class="hero-title">欢迎使用 恋爱大师</div>
      <div class="hero-desc">先到「联系人」页添加第一位联系人，<br>
      或到「聊天分析」页直接粘贴一段聊天记录试试。</div>
    </div>""", unsafe_allow_html=True)
    st.stop()

# ─── KPI 卡片 ──────────────────────────────────────────────
total = len(contacts)
hot = sum(1 for c in contacts if (c.get("interest_score") or 0) >= 7)
cooling = sum(1 for c in contacts if (c.get("momentum") or "") == "falling")
ambiguous = sum(1 for c in contacts if (c.get("ambiguity_percent") or 0) >= 50)

st.markdown('<div class="apple-card">', unsafe_allow_html=True)
col1, col2, col3, col4 = st.columns(4)
with col1:
    st.markdown(f'<div class="kpi"><div class="metric-label">联系人总数</div>'
                f'<div class="metric-value">{total}</div></div>', unsafe_allow_html=True)
with col2:
    st.markdown(f'<div class="kpi"><div class="metric-label">高热度</div>'
                f'<div class="metric-value" style="color:{colors["GREEN"]}">{hot}</div></div>', unsafe_allow_html=True)
with col3:
    st.markdown(f'<div class="kpi"><div class="metric-label">降温中</div>'
                f'<div class="metric-value" style="color:{colors["ORANGE"]}">{cooling}</div></div>', unsafe_allow_html=True)
with col4:
    st.markdown(f'<div class="kpi"><div class="metric-label">暧昧中</div>'
                f'<div class="metric-value" style="color:{colors["BLUE"]}">{ambiguous}</div></div>', unsafe_allow_html=True)
st.markdown('</div>', unsafe_allow_html=True)

# ─── 联系人卡片 ────────────────────────────────────────────
section_label("所有联系人")
cols = st.columns(3)
for i, c in enumerate(contacts):
    stage = c.get("stage", "陌生人")
    interest = c.get("interest_score") or 0
    ambiguity = c.get("ambiguity_percent") or 0
    momentum = c.get("momentum") or "stable"
    momentum_icon = {"rising": "↑", "stable": "→", "falling": "↓"}.get(momentum, "→")
    momentum_color = {"rising": colors["GREEN"], "stable": colors["TEXT_SEC"],
                      "falling": colors["ORANGE"]}.get(momentum, colors["TEXT_SEC"])
    stage_idx = RELATIONSHIP_STAGES.index(stage) if stage in RELATIONSHIP_STAGES else 0
    progress_pct = round(stage_idx / (len(RELATIONSHIP_STAGES) - 1) * 100)

    with cols[i % 3]:
        st.markdown(f"""<div class="apple-card-sm" style="margin-bottom:16px;">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
    <div style="font-size:1.05rem;font-weight:700;">{c['name']}</div>
    <span style="color:{momentum_color};font-size:1.05rem;font-weight:800;">{momentum_icon}</span>
  </div>
  <div style="margin-bottom:12px;"><span class="tag tag-blue">{stage}</span></div>
  <div style="display:flex;justify-content:space-between;">
    <div><div class="metric-label">好感度</div>
      <div style="font-size:1.2rem;font-weight:800;">{interest}<span style="font-size:0.75rem;opacity:0.5"> /10</span></div></div>
    <div><div class="metric-label">暧昧</div>
      <div style="font-size:1.2rem;font-weight:800;">{ambiguity}<span style="font-size:0.75rem;opacity:0.5"> %</span></div></div>
    <div><div class="metric-label">进展</div>
      <div style="font-size:1.2rem;font-weight:800;">{progress_pct}<span style="font-size:0.75rem;opacity:0.5"> %</span></div></div>
  </div>
</div>""", unsafe_allow_html=True)

# ─── 最近分析摘要 ──────────────────────────────────────────
recent = [c for c in contacts if c.get("stage") and c.get("interest_score")][:5]
if recent:
    section_label("最近分析摘要")
    for c in recent:
        st.markdown(
            f'<div class="apple-card-sm" style="display:flex;justify-content:space-between;'
            f'align-items:center;"><span style="font-weight:600;">{c["name"]}</span>'
            f'<span class="tag tag-blue">{c.get("stage", "-")}</span>'
            f'<span style="opacity:0.6;font-size:0.88rem;">好感度 {c.get("interest_score", 0)}/10</span></div>',
            unsafe_allow_html=True,
        )
