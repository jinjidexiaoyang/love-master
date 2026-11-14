"""
admin/pages/3_style_core.py — 风格核心配置(管理端)

(原 pages/5_style_settings.py → admin_pages/ → admin/pages/。
 功能逻辑保持不变,仅:① 鉴权统一 require_admin();② sidebar 指向 admin 页;
 ③ 目录迁移后修正 ROOT/import/page_link。风格核心内部机制 / DNA / style_engine 一字未改。)

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

from database import init_db, get_knowledge  # noqa: E402
from services.style_engine import get_style_engine, STYLE_PROFILE_PATH  # noqa: E402
from config import get_core_dna, save_core_dna  # noqa: E402
from utils.ui_helpers import apply_theme, theme_toggle_button  # noqa: E402
from admin._auth import require_admin  # noqa: E402

st.set_page_config(page_title="风格核心 · 恋爱大师管理后台", page_icon="🧬", layout="wide")

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

st.markdown('<div class="page-title">风格核心</div>', unsafe_allow_html=True)
st.markdown('<div class="page-subtitle">蒸馏你的专属风格 DNA，注入所有 AI 回复</div>', unsafe_allow_html=True)

engine = get_style_engine()
profile = engine.get_style_profile()
dna = engine.get_dna()
has_profile = bool(profile)
has_dna = os.path.exists("data/core_dna.txt") and "暂未蒸馏" not in dna

tab1, tab2, tab3 = st.tabs(["蒸馏风格", "查看 · 编辑", "风格预览"])
# ─── Tab1: 蒸馏 ───────────────────────────────────────────
with tab1:
    st.markdown('<div class="apple-card">', unsafe_allow_html=True)

    all_materials = get_knowledge()
    mat_count = len(all_materials)

    # 状态行
    col_status, col_action = st.columns([3, 2])
    with col_status:
        if has_profile:
            st.markdown('<span class="dna-active">● 风格档案已生成</span>', unsafe_allow_html=True)
        else:
            st.markdown('<span class="dna-inactive">○ 尚未蒸馏</span>', unsafe_allow_html=True)
        st.markdown(f'<div style="margin-top:8px;font-size:0.88rem;opacity:0.6">知识库中有 <strong>{mat_count}</strong> 条素材可用于蒸馏</div>', unsafe_allow_html=True)

    with col_action:
        if mat_count == 0:
            st.button("🧬 开始蒸馏", type="primary", use_container_width=True, disabled=True)
        else:
            if st.button("🧬 开始蒸馏", type="primary", use_container_width=True):
                progress_text = st.empty()
                with st.spinner("AI 正在深度分析素材，提炼风格核心...（约需 30-60 秒）"):
                    def update_progress(msg):
                        progress_text.markdown(f'<div style="opacity:0.6;font-size:0.85rem">{msg}</div>', unsafe_allow_html=True)
                    result = engine.distill_from_materials(progress_callback=update_progress)

                if "error" in result:
                    st.error(result["error"])
                else:
                    st.success("蒸馏完成！风格核心已更新并注入所有 AI 功能")
                    st.rerun()

    st.markdown('</div>', unsafe_allow_html=True)

    # 前置步骤引导（未蒸馏时显示）
    if not has_profile:
        st.markdown('<div class="apple-card">', unsafe_allow_html=True)
        st.markdown("**开始蒸馏前，请先完成以下准备：**")
        steps = [
            ("1", "去「知识库」→「素材库」，上传你的理论资料（推荐：你认可的 game 理论、心态总结）", mat_count > 0),
            ("2", "去「知识库」→「案例库」，添加几个你觉得回复得好的真实案例", False),
            ("3", "素材准备好后，回到这里点击「开始蒸馏」", False),
        ]
        for num, desc, done in steps:
            icon = "✅" if done else "○"
            opacity = "1" if not done else "0.5"
            st.markdown(f'<div style="display:flex;gap:12px;padding:8px 0;font-size:0.9rem;opacity:{opacity}"><span>{icon}</span><span>{desc}</span></div>', unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)
        st.markdown('<div style="font-size:0.75rem;font-weight:600;opacity:0.5;text-transform:uppercase;letter-spacing:0.5px;margin:16px 0 12px 0">当前风格档案</div>', unsafe_allow_html=True)

        sections = [
            ("mindset_core", "🧠 底层心态"),
            ("language_style", "◇ 语言风格"),
            ("preferred_patterns", "✓ 偏好模式"),
            ("forbidden_patterns", "✗ 禁止行为"),
            ("tone_rules", "🎯 语气规则"),
        ]

        col1, col2 = st.columns(2)
        for i, (field, title) in enumerate(sections):
            items = profile.get(field, [])
            if not items:
                continue
            with (col1 if i % 2 == 0 else col2):
                st.markdown('<div class="apple-card">', unsafe_allow_html=True)
                st.markdown(f'<div style="font-size:0.85rem;font-weight:600;margin-bottom:10px">{title}</div>', unsafe_allow_html=True)
                for item in items:
                    st.markdown(f'<div style="font-size:0.88rem;padding:4px 0;opacity:0.85">· {item}</div>', unsafe_allow_html=True)
                st.markdown('</div>', unsafe_allow_html=True)

        if profile.get("escalation_logic"):
            st.markdown('<div class="apple-card">', unsafe_allow_html=True)
            st.markdown('<div style="font-size:0.85rem;font-weight:600;margin-bottom:8px">→ 推进逻辑</div>', unsafe_allow_html=True)
            st.markdown(f'<div style="font-size:0.9rem;line-height:1.6;opacity:0.85">{profile["escalation_logic"]}</div>', unsafe_allow_html=True)
            st.markdown('</div>', unsafe_allow_html=True)

        if profile.get("signature_style"):
            st.markdown('<div class="apple-card">', unsafe_allow_html=True)
            st.markdown('<div style="font-size:0.85rem;font-weight:600;margin-bottom:8px">✦ 标志性风格</div>', unsafe_allow_html=True)
            st.markdown(f'<div style="font-size:0.9rem;line-height:1.6;opacity:0.85">{profile["signature_style"]}</div>', unsafe_allow_html=True)
            st.markdown('</div>', unsafe_allow_html=True)
# ─── Tab2: 编辑 ───────────────────────────────────────────
with tab2:
    edit_tab1, edit_tab2 = st.tabs(["编辑 DNA 文本", "编辑 JSON 档案"])

    with edit_tab1:
        st.markdown('<div class="apple-card">', unsafe_allow_html=True)
        st.caption("直接编辑风格核心文本，保存后立即生效")
        edited_dna = st.text_area("核心DNA", value=dna, height=400, label_visibility="collapsed")
        if st.button("保存 DNA", type="primary"):
            save_core_dna(edited_dna)
            st.success("已保存")
        st.markdown('</div>', unsafe_allow_html=True)

    with edit_tab2:
        st.markdown('<div class="apple-card">', unsafe_allow_html=True)
        st.caption("编辑结构化风格档案（JSON），保存后重新蒸馏规则")
        profile_str = json.dumps(profile, ensure_ascii=False, indent=2) if profile else "{}"
        edited_profile = st.text_area("style_profile.json", value=profile_str, height=400,
                                      label_visibility="collapsed")
        if st.button("保存 JSON 档案", type="primary"):
            try:
                new_profile = json.loads(edited_profile)
                os.makedirs("data", exist_ok=True)
                with open(STYLE_PROFILE_PATH, "w", encoding="utf-8") as f:
                    json.dump(new_profile, f, ensure_ascii=False, indent=2)
                st.success("已保存")
            except json.JSONDecodeError as e:
                st.error(f"JSON 格式错误：{e}")
        st.markdown('</div>', unsafe_allow_html=True)

# ─── Tab3: 风格预览 ───────────────────────────────────────
with tab3:
    st.markdown('<div class="apple-card">', unsafe_allow_html=True)
    st.markdown('<div style="font-size:0.75rem;font-weight:600;opacity:0.5;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:12px">当前注入到所有 AI 的风格上下文</div>', unsafe_allow_html=True)
    st.caption("这是每次调用 AI 时实际传入的风格约束内容")
    style_ctx = engine.get_style_context()
    st.code(style_ctx, language=None)
    st.markdown('</div>', unsafe_allow_html=True)
