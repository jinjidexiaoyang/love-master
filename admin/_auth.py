"""
admin/_auth.py — 管理员后台统一鉴权

下划线前缀:Streamlit 不会把它注册为页面。
每个 admin 页面与 admin/app.py 首行调用 require_admin() 即可。

鉴权规则(沿用原 pages/8 行为):
  - 设置了环境变量 LOVE_MASTER_ADMIN_TOKEN → 必须输入正确口令才放行
  - 未设置 → 本地调试模式,显示 warning 但允许查看
鉴权状态存 st.session_state["admin_authed"],同一浏览器会话内跨页共享。
"""
from __future__ import annotations

import streamlit as st
from config import get_admin_config


def require_admin() -> None:
    """管理员鉴权闸门。未通过则 st.stop()。"""
    cfg = get_admin_config()
    token = cfg.token

    if not token:
        st.warning("LOVE_MASTER_ADMIN_TOKEN 未设置 — 管理后台处于无口令调试模式。"
                   "生产环境请在 .env 中设置 LOVE_MASTER_ADMIN_TOKEN。")
        return

    if st.session_state.get("admin_authed"):
        return

    st.markdown("### 🔒 管理员登录")
    st.caption("恋爱大师 · 内容管理后台 · 仅管理员可访问")
    entered = st.text_input("LOVE_MASTER_ADMIN_TOKEN", type="password", key="admin_token_input")
    if st.button("登录", key="admin_login_btn"):
        if entered == token:
            st.session_state["admin_authed"] = True
            st.rerun()
        else:
            st.error("口令错误")
    st.stop()
