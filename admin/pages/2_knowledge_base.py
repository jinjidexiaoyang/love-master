"""
admin/pages/2_knowledge_base.py — 知识库管理(管理端)

(原 pages/4_knowledge_base.py → admin_pages/ → admin/pages/。
 功能逻辑保持不变,仅:① 鉴权统一 require_admin();② sidebar 指向 admin 页;
 ③ 目录迁移后修正 ROOT/import/page_link。知识库内部机制 / database / ingestion_service 一字未改。)

鉴权:统一由 admin/_auth.require_admin() 处理。
"""
import os
import sys
from pathlib import Path

import streamlit as st
import json

# admin/pages/<page>.py → 项目根 = parents[2];加入 sys.path 并切到根目录。
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from database import init_db, get_knowledge, add_knowledge, get_cases, add_case, get_style_rules, add_style_rule  # noqa: E402
from services.ingestion_service import import_theory_txt  # noqa: E402
from config import GAME_TECHNIQUES  # noqa: E402
from utils.ui_helpers import apply_theme, theme_toggle_button  # noqa: E402
from admin._auth import require_admin  # noqa: E402

st.set_page_config(page_title="知识库 · 恋爱大师管理后台", page_icon="📚", layout="wide")

@st.cache_resource
def setup_db():
    init_db()
setup_db()

theme_toggle_button()
colors = apply_theme()

# ─── 统一鉴权 ─────────────────────────────────────────────
require_admin()

# ─── 管理端侧边栏 ─────────────────────────────────────────
with st.sidebar:
    st.markdown('<div style="padding:8px 0 16px 0;font-size:1.3rem;font-weight:700;">🛠 恋爱大师管理后台</div>',
                unsafe_allow_html=True)
    st.page_link("app.py", label="🏠 Admin 首页")
    st.page_link("pages/1_mentor_ingest.py", label="🎬 导师资源后台")
    st.page_link("pages/2_knowledge_base.py", label="📚 知识库")
    st.page_link("pages/3_style_core.py", label="🧬 风格核心")

st.markdown('<div class="page-title">知识库</div>', unsafe_allow_html=True)
st.markdown('<div class="page-subtitle">素材库 · 案例库 · 风格规则</div>', unsafe_allow_html=True)

tab1, tab2, tab3 = st.tabs(["素材库", "案例库", "风格规则"])
# ─── Tab1: 素材库 ─────────────────────────────────────────
with tab1:
    col_add, col_list = st.columns([2, 3])

    with col_add:
        st.markdown('<div class="apple-card">', unsafe_allow_html=True)
        st.markdown("**添加素材**")
        mat_type = st.selectbox("素材类型", [
            "developer_theory", "developer_case_success",
            "developer_case_failure", "user_chat_import"
        ], format_func=lambda x: {
            "developer_theory": "📖 理论资料",
            "developer_case_success": "✅ 成功案例",
            "developer_case_failure": "❌ 失败案例",
            "user_chat_import": "💬 聊天导入",
        }[x])
        mat_title = st.text_input("标题（可选）", placeholder="例如：推拉理论总结")
        mat_tags = st.multiselect("标签", GAME_TECHNIQUES)
        mat_content = st.text_area("内容", height=200,
                                   placeholder="粘贴理论资料、案例总结、心得体会...")

        col_b1, col_b2 = st.columns(2)
        with col_b1:
            if st.button("直接添加", type="primary", use_container_width=True):
                if mat_content.strip():
                    add_knowledge(mat_type, mat_content, mat_title, mat_tags)
                    st.success("已添加")
                    st.rerun()
        with col_b2:
            if st.button("切块导入", use_container_width=True,
                         help="自动按段落切块，适合长文本"):
                if mat_content.strip():
                    count = import_theory_txt(mat_content, mat_title, mat_type, mat_tags)
                    st.success(f"已切块导入 {count} 段")
                    st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)

    with col_list:
        all_knowledge = get_knowledge()
        type_labels = {
            "developer_theory": "📖 理论",
            "developer_case_success": "✅ 成功",
            "developer_case_failure": "❌ 失败",
            "user_chat_import": "💬 导入",
        }
        st.markdown(f'<div style="font-size:0.85rem;opacity:0.5;margin-bottom:12px">共 {len(all_knowledge)} 条素材</div>', unsafe_allow_html=True)
        if all_knowledge:
            for item in all_knowledge:
                label = type_labels.get(item["category"], item["category"])
                title = item.get("title") or "无标题"
                date = item["created_at"][:10]
                with st.expander(f"{label} {title} · {date}"):
                    st.write(item["content"])
                    tags = json.loads(item.get("tags", "[]"))
                    if tags:
                        st.caption(f"标签：{', '.join(tags)}")
        else:
            st.markdown('<div style="opacity:0.4;text-align:center;padding:40px 0">暂无素材</div>', unsafe_allow_html=True)
# ─── Tab2: 案例库 ─────────────────────────────────────────
with tab2:
    col_add2, col_list2 = st.columns([2, 3])

    with col_add2:
        st.markdown('<div class="apple-card">', unsafe_allow_html=True)
        st.markdown("**添加实战案例**")
        c_title = st.text_input("案例标题", placeholder="例如：推拉反转经典案例")
        c_scenario = st.text_area("场景描述", height=60, placeholder="什么情况下发生的")
        c_her = st.text_input("她说了什么", placeholder="她的原话")
        c_my = st.text_input("我怎么回的", placeholder="你的回复")
        c_outcome = st.text_input("结果如何", placeholder="她的反应 / 后续发展")
        c_tags = st.multiselect("技巧标签", GAME_TECHNIQUES, key="case_tags")
        c_rating = st.slider("案例评分", 1, 5, 4)
        if st.button("保存案例", type="primary", use_container_width=True):
            if c_title.strip() and c_my.strip():
                add_case(c_title, c_scenario, c_my, c_her, c_tags, c_outcome, c_rating)
                st.success("案例已保存")
                st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)

    with col_list2:
        cases = get_cases(limit=50)
        st.markdown(f'<div style="font-size:0.85rem;opacity:0.5;margin-bottom:12px">共 {len(cases)} 个案例</div>', unsafe_allow_html=True)
        if cases:
            for c in cases:
                tags_str = "、".join(json.loads(c.get("technique_tags", "[]")))
                stars = "★" * c["rating"] + "☆" * (5 - c["rating"])
                with st.expander(f"{stars} {c['title']} · {tags_str}"):
                    if c.get("scenario"):
                        st.markdown(f"**场景：** {c['scenario']}")
                    if c.get("her_message"):
                        st.markdown(f"**她说：** {c['her_message']}")
                    st.markdown(f"**我回：** {c['my_reply']}")
                    if c.get("outcome"):
                        st.markdown(f"**结果：** {c['outcome']}")
        else:
            st.markdown('<div style="opacity:0.4;text-align:center;padding:40px 0">暂无案例</div>', unsafe_allow_html=True)

# ─── Tab3: 风格规则 ───────────────────────────────────────
with tab3:
    col_add3, col_list3 = st.columns([2, 3])

    with col_add3:
        st.markdown('<div class="apple-card">', unsafe_allow_html=True)
        st.markdown("**添加风格规则**")
        st.caption("这些规则会注入到所有 AI 生成中，优先级仅次于核心DNA")
        r_type = st.selectbox("规则类型", [
            "mindset", "language_style", "preferred_pattern", "forbidden_pattern", "tone"
        ], format_func=lambda x: {
            "mindset": "🧠 心态",
            "language_style": "◇ 语言风格",
            "preferred_pattern": "✓ 偏好模式",
            "forbidden_pattern": "✗ 禁止行为",
            "tone": "🎯 语气",
        }[x])
        r_content = st.text_input("规则内容", placeholder="例如：不主动问她在干嘛")
        r_priority = st.slider("优先级", 1, 10, 7)
        if st.button("添加规则", type="primary", use_container_width=True):
            if r_content.strip():
                add_style_rule(r_type, r_content, r_priority)
                st.success("规则已添加")
                st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)

    with col_list3:
        rules = get_style_rules()
        type_labels = {
            "mindset": "🧠", "language_style": "◇",
            "preferred_pattern": "✓", "forbidden_pattern": "✗",
            "tone": "🎯", "forbidden": "✗", "required": "✓",
        }
        st.markdown(f'<div style="font-size:0.85rem;opacity:0.5;margin-bottom:12px">共 {len(rules)} 条规则</div>', unsafe_allow_html=True)
        if rules:
            for r in rules:
                icon = type_labels.get(r["rule_type"], "·")
                st.markdown(f'<div class="apple-card-sm" style="display:flex;justify-content:space-between;align-items:center;"><span>{icon} {r["rule_content"]}</span><span style="opacity:0.4;font-size:0.8rem">优先级 {r["priority"]}</span></div>', unsafe_allow_html=True)
        else:
            st.markdown('<div style="opacity:0.4;text-align:center;padding:40px 0">暂无规则，可在「风格核心」页蒸馏后自动生成</div>', unsafe_allow_html=True)
