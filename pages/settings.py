"""
pages/settings.py — 设置页（第三方模型 API 配置）
说明：用户在页面上填 API Key / Base URL / 模型名，保存到 data/api_config.json（本地文件，不入版本控制）。
     环境变量（.env）优先级更高；本页保存的值在环境变量未设置时生效。
     保存后立即生效，无需重启（config.get_*_config() 实时读取）。
"""
import os

import streamlit as st

from services.api_config_service import (
    get_api_config, is_config_file_healthy, save_provider_config, test_connection,
)
from utils.ui_helpers import apply_theme, page_header

apply_theme()

page_header("设置")
st.caption("配置 AI 供应商的 API Key / Base URL / 模型名。保存后立即生效，无需重启。"
           "环境变量（.env）优先级更高；本页保存的值在环境变量未设置时生效。")

if not is_config_file_healthy():
    st.warning("API 配置文件损坏，已按空配置处理。重新保存一次即可恢复。")

_saved = get_api_config()

# ─── Anthropic / Claude ──────────────────────────────────────────────────────
st.markdown('<div class="apple-card">', unsafe_allow_html=True)
st.markdown('<div style="font-size:0.75rem;font-weight:600;opacity:0.5;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:12px">Anthropic / Claude</div>', unsafe_allow_html=True)

_a = _saved["anthropic"]
_a_env = {
    "key": bool(os.environ.get("ANTHROPIC_API_KEY")),
    "base": bool(os.environ.get("ANTHROPIC_BASE_URL")),
    "model": bool(os.environ.get("ANTHROPIC_MODEL")),
}
if _a_env["key"]:
    st.caption("ℹ 检测到环境变量 ANTHROPIC_API_KEY（或相关变量）已设置——环境变量优先于本页保存的值。")

col_a1, col_a2 = st.columns(2)
with col_a1:
    a_base = st.text_input("Base URL（留空走官方端点）",
                           value=_a["base_url"],
                           placeholder="https://api.anthropic.com",
                           key="cfg_a_base")
    a_model = st.text_input("模型名（留空用默认 claude-opus-4-5）",
                            value=_a["model"],
                            placeholder="claude-opus-4-5",
                            key="cfg_a_model")
with col_a2:
    a_key = st.text_input("API Key",
                          type="password",
                          value="",
                          placeholder=("已保存 · 重填可覆盖，留空保留原值" if _a["has_key"] else "sk-ant-..."),
                          key="cfg_a_key")

col_ab1, col_ab2, _ = st.columns([1, 1, 3])
with col_ab1:
    if st.button("保存 Anthropic 设置", type="primary", use_container_width=True, key="cfg_a_save"):
        ok, msg = save_provider_config("anthropic", a_base, a_model, a_key)
        (st.success if ok else st.error)(msg)
        if ok:
            st.rerun()
with col_ab2:
    if st.button("测试连接", use_container_width=True, key="cfg_a_test"):
        with st.spinner("测试中..."):
            ok, msg = test_connection("anthropic")
        (st.success if ok else st.error)(msg)

st.markdown('</div>', unsafe_allow_html=True)

# ─── DeepSeek ────────────────────────────────────────────────────────────────
st.markdown('<div style="height:12px"></div>', unsafe_allow_html=True)
st.markdown('<div class="apple-card">', unsafe_allow_html=True)
st.markdown('<div style="font-size:0.75rem;font-weight:600;opacity:0.5;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:12px">DeepSeek</div>', unsafe_allow_html=True)

_d = _saved["deepseek"]
_d_env = bool(os.environ.get("DEEPSEEK_API_KEY"))
if _d_env:
    st.caption("ℹ 检测到环境变量 DEEPSEEK_API_KEY 已设置——环境变量优先于本页保存的值。")

col_d1, col_d2 = st.columns(2)
with col_d1:
    d_base = st.text_input("Base URL（留空用官方端点）",
                           value=_d["base_url"],
                           placeholder="https://api.deepseek.com",
                           key="cfg_d_base")
    d_model = st.text_input("模型名（留空用默认 deepseek-chat）",
                            value=_d["model"],
                            placeholder="deepseek-chat",
                            key="cfg_d_model")
with col_d2:
    d_key = st.text_input("API Key",
                          type="password",
                          value="",
                          placeholder=("已保存 · 重填可覆盖，留空保留原值" if _d["has_key"] else "必填"),
                          key="cfg_d_key")

col_db1, col_db2, _ = st.columns([1, 1, 3])
with col_db1:
    if st.button("保存 DeepSeek 设置", type="primary", use_container_width=True, key="cfg_d_save"):
        ok, msg = save_provider_config("deepseek", d_base, d_model, d_key)
        (st.success if ok else st.error)(msg)
        if ok:
            st.rerun()
with col_db2:
    if st.button("测试连接", use_container_width=True, key="cfg_d_test"):
        with st.spinner("测试中..."):
            ok, msg = test_connection("deepseek")
        (st.success if ok else st.error)(msg)

st.markdown('</div>', unsafe_allow_html=True)

st.markdown('<div style="opacity:0.35;font-size:0.8rem;margin-top:16px">'
            'API Key 只保存在本机 data/api_config.json，不会上传、不会入库。'
            '</div>', unsafe_allow_html=True)
