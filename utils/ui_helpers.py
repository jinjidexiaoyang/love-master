"""共享工具：CSS 设计系统、通用组件（iOS 风格 · 明暗双主题）"""
import streamlit as st
import os


def apply_theme():
    if "theme" not in st.session_state:
        st.session_state.theme = "dark"

    is_dark = st.session_state.theme == "dark"

    if is_dark:
        BG = "#000000"; CARD_BG = "#1C1C1E"; CARD_BG2 = "#2C2C2E"; BORDER = "#3A3A3C"
        TEXT = "#F2F2F7"; TEXT_SEC = "#8E8E93"; BLUE = "#0A84FF"; BLUE_BG = "#0A2540"
        GREEN = "#30D158"; GREEN_BG = "#0D2E1A"; ORANGE = "#FF9F0A"; ORANGE_BG = "#2E1E00"
        RED = "#FF453A"; RED_BG = "#3A1215"
        TAG_BG = "#2C2C2E"; TAG_TEXT = "#EBEBF5"; INPUT_BG = "#1C1C1E"
        SIDEBAR_BG = "#111111"; SIDEBAR_BR = "#2C2C2E"
        STAGE_FUTURE_BG = "#2C2C2E"; STAGE_FUTURE_TEXT = "#636366"
        PLOT_BG = "#1C1C1E"; PLOT_PAPER = "#1C1C1E"; GRID = "#3A3A3C"
        SHADOW = "0 1px 3px rgba(0,0,0,0.30), 0 8px 24px rgba(0,0,0,0.24)"
        HOVER_BG = "#2C2C2E"
    else:
        BG = "#F5F5F7"; CARD_BG = "#FFFFFF"; CARD_BG2 = "#F2F2F7"; BORDER = "#D2D2D7"
        TEXT = "#1D1D1F"; TEXT_SEC = "#6E6E73"; BLUE = "#0071E3"; BLUE_BG = "#E1EFFF"
        GREEN = "#1A7F3C"; GREEN_BG = "#E3F5E9"; ORANGE = "#A05C00"; ORANGE_BG = "#FFF0DC"
        RED = "#C0291B"; RED_BG = "#FCE8E6"
        TAG_BG = "#E8E8ED"; TAG_TEXT = "#3A3A3C"; INPUT_BG = "#FFFFFF"
        SIDEBAR_BG = "#FFFFFF"; SIDEBAR_BR = "#D2D2D7"
        STAGE_FUTURE_BG = "#E8E8ED"; STAGE_FUTURE_TEXT = "#8E8E93"
        PLOT_BG = "white"; PLOT_PAPER = "white"; GRID = "#F2F2F7"
        SHADOW = "0 1px 3px rgba(0,0,0,0.06), 0 8px 24px rgba(0,0,0,0.05)"
        HOVER_BG = "#F5F5F7"

    st.markdown(f"""<style>
  /* ── 基础 ─────────────────────────────────────────────── */
  html, body, [class*="css"]{{font-family:-apple-system,BlinkMacSystemFont,'PingFang SC','Helvetica Neue','Microsoft YaHei',sans-serif;}}
  .stApp{{background-color:{BG};}}
  .stApp,.stApp p,.stApp span,.stApp div,.stApp label,.stMarkdown{{color:{TEXT}!important;}}
  h1,h2,h3{{letter-spacing:-0.3px;}}
  a{{color:{BLUE}!important;}}
  hr{{border:none;border-top:1px solid {BORDER}!important;}}
  ::-webkit-scrollbar{{width:8px;height:8px;}}
  ::-webkit-scrollbar-thumb{{background:{BORDER};border-radius:8px;}}
  ::-webkit-scrollbar-track{{background:transparent;}}

  /* ── 侧边栏 ──────────────────────────────────────────── */
  section[data-testid="stSidebar"]{{background-color:{SIDEBAR_BG}!important;
    border-right:1px solid {SIDEBAR_BR};min-width:264px!important;max-width:264px!important;}}
  section[data-testid="stSidebar"] *{{color:{TEXT}!important;}}
  section[data-testid="stSidebar"] hr{{border-color:{SIDEBAR_BR}!important;}}
  .brand-name{{font-size:1.35rem;font-weight:800;letter-spacing:-0.4px;color:{TEXT}!important;}}
  .brand-sub{{font-size:0.78rem;color:{TEXT_SEC}!important;margin-top:2px;}}
  [data-testid="stSidebarNav"]{{display:none!important;}}

  /* ── 卡片体系 ────────────────────────────────────────── */
  .apple-card{{background:{CARD_BG};border-radius:18px;padding:24px;margin-bottom:16px;
    box-shadow:{SHADOW};border:1px solid {BORDER};}}
  .apple-card-sm{{background:{CARD_BG};border-radius:14px;padding:16px;margin-bottom:12px;
    border:1px solid {BORDER};transition:background .15s ease;}}
  .apple-card-sm:hover{{background:{HOVER_BG};}}
  .section-label{{font-size:0.72rem;font-weight:700;color:{TEXT_SEC}!important;
    text-transform:uppercase;letter-spacing:0.8px;margin-bottom:10px;}}
  .page-title{{font-size:2.1rem;font-weight:800;color:{TEXT}!important;letter-spacing:-0.6px;
    margin-bottom:4px;line-height:1.15;}}
  .page-subtitle{{font-size:0.95rem;color:{TEXT_SEC}!important;margin-bottom:24px;}}
  .hero{{text-align:center;padding:64px 24px;}}
  .hero-icon{{font-size:3.2rem;margin-bottom:16px;}}
  .hero-title{{font-size:1.6rem;font-weight:800;margin-bottom:8px;color:{TEXT}!important;letter-spacing:-0.4px;}}
  .hero-desc{{font-size:0.95rem;color:{TEXT_SEC}!important;line-height:1.6;}}
  .empty-state{{text-align:center;padding:48px 16px;color:{TEXT_SEC}!important;font-size:0.9rem;}}

  /* ── 指标 ────────────────────────────────────────────── */
  .metric-value{{font-size:2.2rem;font-weight:800;color:{TEXT}!important;letter-spacing:-1px;line-height:1.1;}}
  .metric-label{{font-size:0.72rem;color:{TEXT_SEC}!important;text-transform:uppercase;
    letter-spacing:0.8px;margin-bottom:6px;font-weight:600;}}
  .kpi{{text-align:center;padding:8px 0;}}
  [data-testid="stMetricValue"]{{color:{TEXT}!important;font-weight:700;}}
  [data-testid="stMetricLabel"]{{color:{TEXT_SEC}!important;}}

  /* ── 标签 / 阶段 ─────────────────────────────────────── */
  .tag{{display:inline-block;background:{TAG_BG};color:{TAG_TEXT}!important;padding:4px 12px;
    border-radius:20px;font-size:0.8rem;margin:3px;font-weight:600;}}
  .tag-blue{{background:{BLUE_BG};color:{BLUE}!important;}}
  .tag-green{{background:{GREEN_BG};color:{GREEN}!important;}}
  .tag-orange{{background:{ORANGE_BG};color:{ORANGE}!important;}}
  .tag-red{{background:{RED_BG};color:{RED}!important;}}
  .stage-item{{text-align:center;padding:9px 4px;border-radius:10px;font-size:0.82rem;font-weight:600;}}
  .stage-active{{background:{BLUE};color:#FFFFFF!important;box-shadow:0 2px 8px {BLUE}55;}}
  .stage-done{{background:{BLUE_BG};color:{BLUE}!important;}}
  .stage-future{{background:{STAGE_FUTURE_BG};color:{STAGE_FUTURE_TEXT}!important;}}
  .dna-active{{display:inline-flex;align-items:center;gap:6px;background:{GREEN_BG};color:{GREEN}!important;
    padding:4px 12px;border-radius:20px;font-size:0.78rem;font-weight:700;}}
  .dna-inactive{{display:inline-flex;align-items:center;gap:6px;background:{ORANGE_BG};color:{ORANGE}!important;
    padding:4px 12px;border-radius:20px;font-size:0.78rem;font-weight:700;}}

  /* ── 回复卡 ──────────────────────────────────────────── */
  .reply-card{{background:{CARD_BG};border:1px solid {BORDER};border-radius:14px;
    padding:16px 20px;margin:8px 0;}}
  .reply-label{{font-size:0.7rem;font-weight:700;color:{TEXT_SEC}!important;
    text-transform:uppercase;letter-spacing:0.8px;margin-bottom:6px;}}
  .reply-text{{font-size:1rem;color:{TEXT}!important;line-height:1.6;margin-bottom:8px;}}
  .reply-strategy{{font-size:0.82rem;color:{BLUE}!important;font-weight:600;}}

  /* ── 输入组件 ────────────────────────────────────────── */
  .stTextInput>div>div>input,.stTextArea>div>div>textarea,.stSelectbox>div>div,
  [data-baseweb="select"]>div{{border-radius:12px!important;border:1px solid {BORDER}!important;
    color:{TEXT}!important;background:{INPUT_BG}!important;}}
  .stTextInput>div>div>input:focus,.stTextArea>div>div>textarea:focus{{
    border-color:{BLUE}!important;box-shadow:0 0 0 3px {BLUE}33!important;}}
  [data-baseweb="popover"]>div{{background:{CARD_BG}!important;border:1px solid {BORDER}!important;
    border-radius:12px!important;box-shadow:{SHADOW};}}
  .stSlider>div[data-baseweb="slider"]{{color:{BLUE};}}
  [role="colorpicker"],[data-testid="stDateInput"] input{{border-radius:12px!important;}}

  /* ── 按钮 ────────────────────────────────────────────── */
  .stButton>button{{border-radius:12px;font-weight:600;font-size:0.9rem;border:1px solid {BORDER};
    background:{CARD_BG};color:{TEXT}!important;padding:8px 18px;
    transition:all .15s ease;}}
  .stButton>button:hover{{background:{HOVER_BG};border-color:{TEXT_SEC};}}
  .stButton>button[kind="primary"]{{background:{BLUE}!important;color:#FFFFFF!important;
    border:none!important;box-shadow:0 2px 10px {BLUE}44;}}
  .stButton>button[kind="primary"]:hover{{filter:brightness(1.1);}}
  .stDownloadButton>button{{border-radius:12px;font-weight:600;border:1px solid {BORDER};
    background:{CARD_BG};color:{TEXT}!important;}}

  /* ── Tabs ────────────────────────────────────────────── */
  .stTabs [data-baseweb="tab-list"]{{background:transparent;border-bottom:1px solid {BORDER};gap:4px;}}
  .stTabs [data-baseweb="tab"]{{color:{TEXT_SEC}!important;font-weight:600;background:transparent!important;
    border-radius:10px 10px 0 0;padding:10px 20px;}}
  .stTabs [data-baseweb="tab"]:hover{{color:{TEXT}!important;background:{HOVER_BG}!important;}}
  .stTabs [aria-selected="true"]{{color:{BLUE}!important;border-bottom:2px solid {BLUE}!important;}}
  .stTabs [data-baseweb="tab-highlight"],.stTabs [data-baseweb="tab-border"]{{display:none;}}

  /* ── Expander（新版 details 结构 + 旧版兼容）──────────── */
  [data-testid="stExpander"]{{background:{CARD_BG};border:1px solid {BORDER}!important;
    border-radius:14px!important;box-shadow:none;overflow:hidden;}}
  [data-testid="stExpander"] summary{{padding:14px 20px!important;font-weight:600;}}
  [data-testid="stExpander"] details>summary{{color:{TEXT}!important;}}
  [data-testid="stExpander"]>details>div{{background:transparent;}}
  .streamlit-expanderHeader{{background:{CARD_BG2}!important;color:{TEXT}!important;
    border-radius:10px;border:1px solid {BORDER};}}
  .streamlit-expanderContent{{background:{CARD_BG}!important;border:1px solid {BORDER};
    color:{TEXT}!important;}}

  /* ── 提示条 ──────────────────────────────────────────── */
  .stAlert{{border-radius:14px!important;border:1px solid {BORDER};}}
  [data-testid="stAlert"]{{border-radius:14px!important;}}

  /* ── 聊天 ────────────────────────────────────────────── */
  [data-testid="stChatMessage"]{{background:{CARD_BG};border:1px solid {BORDER};
    border-radius:14px;padding:12px 16px;}}
  [data-testid="stChatInput"] textarea{{border-radius:14px!important;}}

  /* ── 表格 ────────────────────────────────────────────── */
  [data-testid="stDataFrame"]{{border:1px solid {BORDER};border-radius:14px;overflow:hidden;}}
  [data-testid="stTable"]{{border:1px solid {BORDER};border-radius:14px;}}

  /* ── 仪表元素 ────────────────────────────────────────── */
  [data-testid="stSpinner"],[data-testid="stStatusWidget"]{{color:{BLUE};}}
  [data-testid="stHeader"]{{background:transparent;}}
  [data-testid="stToolbar"]{{display:none;}}
  #MainMenu,footer,header{{visibility:hidden;}}
  .block-container{{padding-top:1.2rem;max-width:1200px;}}
  [data-testid="collapsedControl"]{{display:none!important;}}
  button[data-testid="baseButton-header"]{{display:none!important;}}
</style>""", unsafe_allow_html=True)

    return {
        "BG": BG, "CARD_BG": CARD_BG, "CARD_BG2": CARD_BG2, "BORDER": BORDER,
        "TEXT": TEXT, "TEXT_SEC": TEXT_SEC, "BLUE": BLUE, "BLUE_BG": BLUE_BG,
        "GREEN": GREEN, "GREEN_BG": GREEN_BG, "ORANGE": ORANGE, "ORANGE_BG": ORANGE_BG,
        "RED": RED, "RED_BG": RED_BG,
        "TAG_BG": TAG_BG, "TAG_TEXT": TAG_TEXT, "INPUT_BG": INPUT_BG,
        "PLOT_BG": PLOT_BG, "PLOT_PAPER": PLOT_PAPER, "GRID": GRID,
        "STAGE_FUTURE_BG": STAGE_FUTURE_BG, "STAGE_FUTURE_TEXT": STAGE_FUTURE_TEXT,
    }


def theme_toggle_in_sidebar():
    """侧边栏底部的明暗主题切换按钮。"""
    with st.sidebar:
        st.markdown("<hr style='border:none;border-top:1px solid rgba(128,128,128,0.2);margin:12px 0'>",
                    unsafe_allow_html=True)
        icon = "☀️ 浅色模式" if st.session_state.get("theme") == "dark" else "🌙 深色模式"
        if st.button(icon, use_container_width=True, key="theme_toggle"):
            st.session_state.theme = "light" if st.session_state.get("theme") == "dark" else "dark"
            st.rerun()


def theme_toggle_button():
    """页面顶部明暗切换按钮（admin 后台等独立入口使用，保持旧行为）。"""
    _, col_btn = st.columns([20, 1])
    with col_btn:
        icon = "☀️" if st.session_state.get("theme") == "dark" else "🌙"
        if st.button(icon, key="theme_toggle"):
            st.session_state.theme = "dark" if st.session_state.get("theme") == "light" else "light"
            st.rerun()


def sidebar_brand():
    """侧边栏品牌区（Logo + 风格核心状态）。在 st.navigation 主脚本中调用。"""
    with st.sidebar:
        st.markdown("""<div style="padding:4px 0 14px 0;" translate="no">
          <div class="brand-name" translate="no">✦ 恋爱大师</div>
          <div class="brand-sub">AI 关系研判 · 开源纪念版</div>
        </div>""", unsafe_allow_html=True)
        dna_exists = os.path.exists(os.path.join("data", "core_dna.txt"))
        if dna_exists:
            st.markdown('<span class="dna-active">● 风格核心已激活</span>', unsafe_allow_html=True)
        else:
            st.markdown('<span class="dna-inactive">○ 风格核心未配置</span>', unsafe_allow_html=True)
        st.markdown("<hr style='border:none;border-top:1px solid rgba(128,128,128,0.2);margin:12px 0'>",
                    unsafe_allow_html=True)


def page_header(title: str, subtitle: str = ""):
    """统一页头。"""
    st.markdown(f'<div class="page-title">{title}</div>', unsafe_allow_html=True)
    if subtitle:
        st.markdown(f'<div class="page-subtitle">{subtitle}</div>', unsafe_allow_html=True)


def section_label(text: str):
    """统一小节标签（替代各页重复的 inline style div）。"""
    st.markdown(f'<div class="section-label">{text}</div>', unsafe_allow_html=True)
