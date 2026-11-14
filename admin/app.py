"""
admin/app.py — 恋爱大师 · 内容管理后台 入口(管理员专用,独立启动)

启动:
  streamlit run admin/app.py --server.port 8502

与用户端 app.py 物理隔离:
  - 用户端 app.py 只扫描同级 pages/ 目录
  - 管理端入口 admin/app.py 的子页面在 admin/pages/ 目录,不会被用户端加载
  - 管理页面不会出现在用户端 sidebar,URL 也无法从用户端路由到
  - st.page_link 只识别"入口文件 + 入口同级 pages/",因此管理端必须自带
    admin/pages/,这也是本次从 admin_pages/ 迁移到 admin/pages/ 的原因。

管理后台模块(admin/pages/):
  1_mentor_ingest    导师资源管理 / 采集 / 任务队列 / 卡片审核 / 脱敏(P4C)
  2_knowledge_base   知识库
  3_style_core       风格核心
  4_mentor_pipeline  导师采集流水线控制台(P4C-Pipeline · 全流程进度面板)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import streamlit as st

# admin/app.py 在子目录,项目根 = parents[1];把根加入 sys.path 并切到根目录,
# 保证 services/scripts/config/utils 可 import,data/ 等相对路径按项目根解析。
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from database import init_db  # noqa: E402
from utils.ui_helpers import apply_theme, theme_toggle_button  # noqa: E402
from admin._auth import require_admin  # noqa: E402

st.set_page_config(
    page_title="恋爱大师管理后台",
    page_icon="🛠",
    layout="wide",
    initial_sidebar_state="expanded",
)


@st.cache_resource
def setup_db():
    init_db()


setup_db()

theme_toggle_button()
apply_theme()

# ─── 统一鉴权(未通过则 st.stop)─────────────────────────
require_admin()

# ─── 管理端侧边栏 ─────────────────────────────────────────
with st.sidebar:
    st.markdown(
        '<div style="padding:8px 0 16px 0;" translate="no">'
        '<div style="font-size:1.3rem;font-weight:700;letter-spacing:-0.3px;" translate="no">🛠 恋爱大师管理后台</div>'
        '<div style="font-size:0.78rem;opacity:0.5;margin-top:2px;">内容蒸馏与导师资源管理后台</div>'
        '</div>',
        unsafe_allow_html=True,
    )
    st.markdown("<hr style='border:none;border-top:1px solid rgba(128,128,128,0.2);margin:12px 0'>",
                unsafe_allow_html=True)
    st.page_link("app.py", label="🏠 Admin 首页")
    st.page_link("pages/1_mentor_ingest.py", label="🎬 导师资源后台")
    st.page_link("pages/2_knowledge_base.py", label="📚 知识库")
    st.page_link("pages/3_style_core.py", label="🧬 风格核心")
    st.page_link("pages/4_mentor_pipeline.py", label="🛠 流水线控制台")

# ─── Admin Dashboard ──────────────────────────────────────
st.markdown('<div class="page-title">恋爱大师 · 内容管理后台</div>', unsafe_allow_html=True)
st.markdown('<div class="page-subtitle">管理员专用 · 与用户端物理隔离 · 不直写正式知识库</div>',
            unsafe_allow_html=True)

st.markdown('<div class="apple-card">', unsafe_allow_html=True)
st.markdown("#### 后台模块")
st.markdown(
    "- 🎬 **导师资源后台**:内容采集 / ASR 转写 / 卡片化 / 任务队列 / 审核合并 / 导师身份脱敏(P4C)\n"
    "- 🛠 **流水线控制台**:转写 / 切块 / 卡片化 / 合并 / 画像刷新 全流程进度面板(P4C-Pipeline)\n"
    "- 📚 **知识库**:素材库 / 案例库 / 风格规则\n"
    "- 🧬 **风格核心**:DNA 蒸馏 / 编辑 / 风格预览\n"
)
st.caption("从左侧导航进入各模块。所有导师内容处理走 staging → 审核 → 合并,不污染正式 cards/chunks/profiles。")
st.markdown('</div>', unsafe_allow_html=True)

st.info("提示:管理后台与用户端是两个独立 Streamlit 应用。用户端启动 `streamlit run app.py`;"
        "管理端启动 `streamlit run admin/app.py --server.port 8502`。")
