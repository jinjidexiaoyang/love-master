"""
app.py — 恋爱大师 主入口（st.navigation 多页应用）

页面结构：总览 / 联系人 / 聊天分析 / 导师问答 / 设置
"""
import streamlit as st

from utils.ui_helpers import apply_theme, sidebar_brand, theme_toggle_in_sidebar

st.set_page_config(
    page_title="恋爱大师",
    page_icon="✦",
    layout="wide",
    initial_sidebar_state="expanded",
)

apply_theme()
sidebar_brand()

home = st.Page("pages/home.py", title="总览", icon="📊", default=True)
contacts = st.Page("pages/contacts.py", title="联系人", icon="👤")
analysis = st.Page("pages/analysis.py", title="聊天分析", icon="💬")
mentor = st.Page("pages/mentor_tutor.py", title="导师问答", icon="🧠")
settings = st.Page("pages/settings.py", title="设置", icon="⚙")

nav = st.navigation([home, contacts, analysis, mentor, settings])
nav.run()

theme_toggle_in_sidebar()
