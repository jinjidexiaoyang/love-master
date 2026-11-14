"""
admin/pages/1_mentor_ingest.py — 导师资源管理与内容蒸馏后台(P4C)

(原 pages/8_admin_mentor_ingest.py → admin_pages/ → admin/pages/。
 功能逻辑保持不变,仅:① 鉴权统一 require_admin();② sidebar 指向 admin 页;
 ③ 目录迁移后修正 ROOT/import/page_link。)

鉴权:统一由 admin/_auth.require_admin() 处理。

功能模块:
  1) 导师身份(脱敏映射)管理
  2) 新建采集任务(local / url / douyin / bilibili)
  3) 扫描/采集报告
  4) 转写(任务队列执行)
  5) 卡片化(任务队列执行)
  6) 卡片审核(P1 逐条审核闸门)
  7) 审核合并(dry-run + 双重确认真实合并)
  8) 批次历史 + 任务汇总
  9) 工具配置
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from pathlib import Path

import streamlit as st

# admin/pages/<page>.py → 项目根 = parents[2];加入 sys.path 并切到根目录。
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from services.mentor_ingest_service import (  # noqa: E402
    INGEST_ROOT, RAW_LOCAL, RAW_DOUYIN, RAW_BILI, STAGING_ROOT,
    load_settings, save_settings,
    create_batch, scan_local_folder,
    collect_single_url, collect_douyin_homepage, collect_bilibili_homepage,
    list_batches, load_manifest, load_source_items,
    batch_dir, set_status, attach_local_file_to_batch,
)
from services import mentor_identity_service as ident  # noqa: E402
from services import mentor_task_service as tasksvc  # noqa: E402
from services import mentor_card_review_service as review  # noqa: E402
from services import platform_collect_service as collect  # noqa: E402
from services import mentor_autoingest_service as autoingest  # noqa: E402
from services import platform_download_service as pldl  # noqa: E402
from utils.ui_helpers import apply_theme, theme_toggle_button  # noqa: E402
from admin._auth import require_admin  # noqa: E402

st.set_page_config(page_title="导师后台 · 恋爱大师管理后台", page_icon="🎬", layout="wide")
theme_toggle_button()
apply_theme()

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
    st.page_link("pages/4_mentor_pipeline.py", label="🛠 流水线控制台")

st.markdown('<div class="page-title">导师资源管理与内容蒸馏后台</div>', unsafe_allow_html=True)
st.markdown('<div class="page-subtitle">仅管理员可见 · 主流程：本地素材自动入库 → batch → 转写 → 卡片化 → 卡片审核 → 审核合并</div>',
            unsafe_allow_html=True)

# ── Tab 可见性开关（改为 True 可恢复对应入口）──────────────
SHOW_ADVANCED_INGEST_TABS = False   # 新建采集 / 扫描报告
SHOW_EXPERIMENTAL_P2A = False       # 实验:URL采集(P2A)
SHOW_PLATFORM_DL = True             # 平台素材下载(P4D)

# 主流程 tab 定义（有序，key → 显示名）
_TAB_DEFS: list[tuple[str, str]] = [
    ("auto_ingest",   "自动入库"),
    ("identity",      "导师身份"),
    ("transcribe",    "转写"),
    ("build_cards",   "卡片化"),
    ("card_review",   "卡片审核"),
    ("approve",       "审核合并"),
    ("history",       "批次历史"),
    ("config",        "工具配置"),
]
if SHOW_ADVANCED_INGEST_TABS:
    _TAB_DEFS.insert(2, ("new_collect", "新建采集"))
    _TAB_DEFS.insert(3, ("scan_report", "扫描报告"))
if SHOW_EXPERIMENTAL_P2A:
    _TAB_DEFS.append(("p2a", "实验:URL采集(P2A)"))
if SHOW_PLATFORM_DL:
    _TAB_DEFS.append(("platform_dl", "平台素材下载"))

_tab_keys   = [k for k, _ in _TAB_DEFS]
_tab_labels = [lbl for _, lbl in _TAB_DEFS]
_tab_objects = st.tabs(_tab_labels)
tab_map: dict[str, object] = dict(zip(_tab_keys, _tab_objects))

# ─── Tab: 自动入库(P4C 主入口 · Auto-1 扫描候选)──────────
with tab_map["auto_ingest"]:
    st.subheader("自动入库(本地文件夹驱动)")
    st.caption("主流程:外部工具下载公开/授权视频 → 按规则命名文件夹 → 放入主素材目录 → 本页扫描入库。"
               "platform 等来源信息仅管理员端可见,不进入用户端。")

    _ai_settings = load_settings()
    _ai_root_default = _ai_settings.get("ingest", {}).get("auto_ingest_root", "")

    st.markdown("#### 1) 主素材目录")
    st.caption("命名规则:`真实导师名__前台化名__风格标签__平台`("
               "如 `张三__灰狼__约会节奏控制型__bilibili`)。平台白名单:bilibili / douyin / local / unknown。")
    ai_root = st.text_input("auto_ingest_root(主目录)", _ai_root_default, key="ai_root")
    rc1, rc2 = st.columns([1, 1])
    with rc1:
        if st.button("💾 保存主目录", key="ai_save_root"):
            s = load_settings()
            s.setdefault("ingest", {})["auto_ingest_root"] = ai_root.strip()
            save_settings(s)
            st.success("已保存 auto_ingest_root")
            st.rerun()
    with rc2:
        scan_clicked = st.button("🔍 扫描主目录", key="ai_scan")

    if scan_clicked:
        st.session_state["ai_scan_result"] = autoingest.scan_auto_ingest_root(ai_root.strip())

    _scan = st.session_state.get("ai_scan_result")
    st.markdown("#### 2) 候选文件夹")
    if _scan is None:
        st.info("点「扫描主目录」枚举一级子文件夹候选。本阶段仅展示,不创建 batch、不写身份、不入库。")
    else:
        if not _scan.get("exists"):
            st.warning(" / ".join(_scan.get("warnings") or ["主目录不可用"]))
        for w in _scan.get("warnings", []):
            st.caption(f"⚠ {w}")

        cands = _scan.get("candidates", [])
        if cands:
            _STAT_ICON = {"ready": "✅", "needs_info": "⚠", "empty": "⬜", "invalid": "❌"}
            st.dataframe([{
                "状态": f"{_STAT_ICON.get(c['status'],'?')} {c['status']}",
                "文件夹": c["folder_name"],
                "真实导师": c["parsed_real_mentor"],
                "前台化名": c["parsed_public_name"],
                "风格标签": c["parsed_style_label"],
                "platform(仅后台)": c["parsed_platform"],
                "视频": c["video_count"], "音频": c["audio_count"], "文本": c["text_count"],
                "命中导师": (c["matched_identity"] or {}).get("public_mentor_id", "") if c["matched_identity"] else "",
            } for c in cands], use_container_width=True, hide_index=True)

            st.markdown("##### 候选详情")
            for c in cands:
                icon = _STAT_ICON.get(c["status"], "?")
                with st.expander(f"{icon} {c['folder_name']} · {c['status']} · "
                                 f"{c['total_supported_count']} 个素材文件"):
                    dc = st.columns(4)
                    dc[0].write(f"**真实导师**: {c['parsed_real_mentor'] or ('(简化命名，待确认)' if c.get('is_simplified') else '-')}")
                    dc[1].write(f"**前台化名/网名**: {c['parsed_public_name'] or '-'}")
                    dc[2].write(f"**风格标签**: {c['parsed_style_label'] or '-'}")
                    dc[3].write(f"**platform**: {c['parsed_platform']} 🔒仅管理员端")
                    if c.get("is_simplified"):
                        st.caption("📌 简化命名：平台网名 / 前台名已从文件夹读取，真实导师名待确认")
                    st.caption(f"folder_path: {c['folder_path']}")
                    st.caption(f"视频 {c['video_count']} · 音频 {c['audio_count']} · 文本 {c['text_count']} · "
                               f"合计 {c['total_supported_count']}")

                    mv = c.get("matched_identity")
                    if mv:
                        st.success(f"命中已有导师:{mv.get('public_mentor_id','')} · "
                                   f"{mv.get('public_name','')}({mv.get('style_label','')})")
                        if mv.get("can_inherit_public_name"):
                            st.caption("→ 可继承 public_name")
                        if mv.get("can_inherit_style_label"):
                            st.caption("→ 可继承 style_label")
                    else:
                        st.caption("未命中已有导师")

                    for w in c.get("warnings", []):
                        st.caption(f"⚠ {w}")

                    cid = c["candidate_id"]
                    can_create = c["status"] in ("ready", "needs_info")
                    batch_result_key = f"ai_batch_result_{cid}"

                    if c["status"] == "needs_info":
                        if c.get("is_simplified"):
                            st.warning("⚠ 简化命名，导师身份待确认：请补充真实导师名，或选择/创建导师身份后创建 batch。")
                        else:
                            st.warning("⚠ 导师身份待确认：public_name 缺失或尚未匹配已有导师，创建 batch 后需在后台补全再转写。")

                    st.markdown("**操作**")

                    # ── Auto-7: 加入已有导师并创建 batch ─────────────────
                    join_result_key = f"ai_join_result_{cid}"
                    if can_create:
                        st.markdown("**加入已有导师**")
                        _all_identities = ident.list_identities()
                        if _all_identities:
                            # 下拉项：public_mentor_id · public_name · style_label · real_mentor_name
                            _id_options = [
                                f"{it.get('public_mentor_id','')} · {it.get('public_name','')} · "
                                f"{it.get('style_label','')} · {it.get('real_mentor_name','')}"
                                for it in _all_identities
                            ]
                            # 默认选中 matched_identity
                            _default_idx = 0
                            if mv:
                                _matched_pid = mv.get("public_mentor_id", "")
                                for _i, _it in enumerate(_all_identities):
                                    if _it.get("public_mentor_id", "") == _matched_pid:
                                        _default_idx = _i
                                        break
                            _selected_label = st.selectbox(
                                "选择已有导师",
                                _id_options,
                                index=_default_idx,
                                key=f"ai_join_sel_{cid}",
                            )
                            _selected_identity = _all_identities[_id_options.index(_selected_label)]
                            if st.button("加入已有导师并创建 batch", key=f"ai_join_{cid}"):
                                _join_res = autoingest.create_auto_ingest_batch(
                                    c, mentor_identity=_selected_identity
                                )
                                st.session_state[join_result_key] = {
                                    "result": _join_res,
                                    "identity": _selected_identity,
                                }
                                if _join_res.get("ok"):
                                    # 把成功的 batch 结果同步到 _br 面板，触发后续 Batch 状态/转写/卡片化/安全总览
                                    st.session_state[batch_result_key] = _join_res
                        else:
                            st.caption("暂无已有导师身份，请先在「导师身份」tab 创建。")
                            st.button("加入已有导师并创建 batch", key=f"ai_join_{cid}", disabled=True)

                        _jr = st.session_state.get(join_result_key)
                        if _jr is not None:
                            _jr_res = _jr.get("result", {})
                            _jr_ident = _jr.get("identity", {})
                            if _jr_res.get("ok"):
                                st.success(
                                    f"✅ 已加入导师并创建 batch：`{_jr_res['batch_id']}` · "
                                    f"{_jr_ident.get('public_mentor_id','')} · "
                                    f"{_jr_ident.get('public_name','')}"
                                )
                                st.caption(f"batch_id: {_jr_res['batch_id']}")
                                st.caption(f"public_mentor_id: {_jr_ident.get('public_mentor_id','')}")
                                st.caption(f"public_name: {_jr_ident.get('public_name','')}")
                            else:
                                st.error(f"创建失败：{_jr_res.get('error', '未知错误')}")
                                for w in _jr_res.get("warnings", []):
                                    st.caption(f"⚠ {w}")
                    else:
                        st.button("加入已有导师并创建 batch",
                                  key=f"ai_join_{cid}", disabled=True)
                        st.caption(f"⚠ 候选状态为 {c['status']}，不允许加入已有导师。")

                    # 创建 batch（始终保留，文案随是否命中导师而变）
                    _batch_label = "为当前导师创建 batch" if mv else "创建 batch"
                    if st.button(_batch_label, key=f"ai_batch_{cid}", disabled=not can_create):
                        st.session_state[batch_result_key] = autoingest.create_auto_ingest_batch(c)

                    # 一键入库（cloud 2C 预留，尚未实现）
                    st.button(
                        "☁ 一键入云端（Phase 2C · 未实现）",
                        key=f"ai_run_{c['candidate_id']}",
                        disabled=True,
                        help=(
                            "云端 upload_approved 属于 Phase 2C，尚未实现，按钮保持禁用。\n"
                            "当前入库走本地流程:本 batch 需先完成「转写 → 卡片化 → 卡片审核通过 → "
                            "审核合并」。真实合并入口在下方「审核前安全总览」及「审核合并」tab，"
                            "不受此云端按钮影响。"
                        ),
                    )
                    st.caption("ℹ 本地入库流程在下方按状态逐步开放;此云端按钮(Phase 2C)尚未实现，与本地流程互不影响。")

                    # ── Auto-8: 导师身份建议入口 ─────────────────────────────
                    if mv and can_create:
                        st.info("✅ 已命中导师身份，不需要生成新身份建议。")
                    elif not mv and can_create:
                        suggest_key = f"ai_suggest_{cid}"
                        suggest_result_key = f"ai_suggest_result_{cid}"
                        st.markdown("---")
                        st.markdown("**AI 生成导师身份建议**")
                        st.caption("基于文件夹信息与已有导师列表生成建议，仅供参考。不写入导师库，不创建 batch。")
                        if st.button("✨ 生成导师身份建议", key=suggest_key):
                            try:
                                _sugg = autoingest.suggest_mentor_identity(c)
                            except Exception as _se:
                                _sugg = {
                                    "ok": False,
                                    "suggested_public_name": "",
                                    "suggested_style_label": "",
                                    "reasoning": "",
                                    "conflict_warnings": [],
                                    "error": str(_se),
                                }
                            st.session_state[suggest_result_key] = _sugg

                        _sugg_res = st.session_state.get(suggest_result_key)
                        if _sugg_res:
                            if not _sugg_res.get("ok", True):
                                st.error(f"生成建议失败：{_sugg_res.get('error', '未知错误')}")
                            else:
                                _src = _sugg_res.get("source", "mock")
                                if _src == "llm":
                                    st.success("✅ 建议来自 DeepSeek LLM")
                                elif _src == "mock_fallback":
                                    st.warning("⚠ LLM 调用失败，以下为本地 fallback 建议")
                                else:
                                    st.info("ℹ 建议来自本地规则（未配置 DEEPSEEK_API_KEY）")
                                sg1, sg2 = st.columns(2)
                                sg1.markdown(f"**建议 public_name**：{_sugg_res['suggested_public_name']}")
                                sg2.markdown(f"**建议 style_label**：{_sugg_res['suggested_style_label']}")
                                st.caption(f"推理：{_sugg_res['reasoning']}")
                                for _cw in _sugg_res.get("conflict_warnings", []):
                                    st.warning(f"⚠ {_cw}")

                                # 一键填入：写入 session_state key，让表单字段读到预填值
                                if st.button("↓ 一键填入下方表单", key=f"ai_suggest_fill_{cid}"):
                                    st.session_state[f"ni_pname_{cid}"]  = _sugg_res["suggested_public_name"]
                                    st.session_state[f"ni_slabel_{cid}"] = _sugg_res["suggested_style_label"]

                    # ── Auto-4: 无 matched_identity 时显示「创建新导师身份并创建 batch」区域 ──
                    if mv:
                        st.caption("已命中导师身份，创建新导师入口已隐藏。")
                    elif can_create:
                        new_id_result_key = f"ai_new_identity_result_{cid}"
                        with st.expander("➕ 创建新导师身份并创建 batch", expanded=False):
                            st.caption("填写新导师基本信息，提交后写入 mentor_identity_map 并自动创建 batch。不 approve，不转写，不卡片化。")
                            with st.form(key=f"new_identity_form_{cid}"):
                                nf1, nf2 = st.columns(2)
                                with nf1:
                                    ni_public_name = st.text_input(
                                        "public_name（前台化名）*",
                                        value=c.get("parsed_public_name") or "",
                                        key=f"ni_pname_{cid}",
                                    )
                                    ni_style_label = st.text_input(
                                        "style_label",
                                        value=c.get("parsed_style_label") or "",
                                        key=f"ni_slabel_{cid}",
                                    )
                                    ni_visibility = st.selectbox(
                                        "visibility",
                                        ["public", "hidden"],
                                        index=0,
                                        key=f"ni_vis_{cid}",
                                    )
                                with nf2:
                                    ni_real_name = st.text_input(
                                        "real_mentor_name（后台真实）*",
                                        value=c.get("parsed_real_mentor") or "",
                                        key=f"ni_rname_{cid}",
                                    )
                                    ni_platform = st.text_input(
                                        "platform（仅后台）",
                                        value=c.get("parsed_platform") or "",
                                        key=f"ni_plat_{cid}",
                                    )
                                ni_submitted = st.form_submit_button("创建导师身份并创建 batch")

                            if ni_submitted:
                                _err = []
                                if not (ni_public_name or "").strip():
                                    _err.append("public_name 不能为空")
                                if not (ni_real_name or "").strip():
                                    _err.append("real_mentor_name 不能为空")
                                if _err:
                                    for e in _err:
                                        st.error(e)
                                else:
                                    try:
                                        new_identity = ident.upsert_identity(
                                            public_name=ni_public_name.strip(),
                                            style_label=(ni_style_label or "").strip(),
                                            real_mentor_name=ni_real_name.strip(),
                                            visibility=ni_visibility,
                                            platform=(ni_platform or "").strip(),
                                        )
                                        _batch_res = autoingest.create_auto_ingest_batch(c, mentor_identity=new_identity)
                                        st.session_state[new_id_result_key] = {
                                            "identity": new_identity,
                                            "batch": _batch_res,
                                        }
                                    except Exception as _ex:
                                        st.error(f"创建失败：{_ex}")

                            _nir = st.session_state.get(new_id_result_key)
                            if _nir:
                                _ni = _nir.get("identity", {})
                                _nb = _nir.get("batch", {})
                                if _nb.get("ok"):
                                    st.success(
                                        f"✅ 新导师身份已创建：`{_ni.get('public_mentor_id','')}` · "
                                        f"{_ni.get('public_name','')} · batch: `{_nb.get('batch_id','')}`"
                                    )
                                    st.caption(f"public_mentor_id: {_ni.get('public_mentor_id','')}")
                                    st.caption(f"public_name: {_ni.get('public_name','')}")
                                    st.caption(f"batch_id: {_nb.get('batch_id','')}")
                                else:
                                    st.error(f"batch 创建失败：{_nb.get('error','未知错误')}")
                                    for w in _nb.get("warnings", []):
                                        st.caption(f"⚠ {w}")

                    _br = st.session_state.get(batch_result_key)
                    if _br is not None:
                        if _br.get("ok"):
                            st.success(f"✅ batch 已创建：`{_br['batch_id']}`")
                            st.caption(f"batch_dir: {_br['batch_dir']}")
                            st.caption(f"staging_dir: {_br['staging_dir']}")
                            for w in _br.get("warnings", []):
                                st.caption(f"⚠ {w}")

                            # ── Auto-3: batch 后续操作区 ──────────────────
                            bid = _br["batch_id"]
                            _m = load_manifest(bid)
                            if _m:
                                _items = load_source_items(bid)
                                _n_total = len(_items)
                                _n_transcribed = sum(1 for it in _items if it.get("status") == "transcribed")
                                _n_carded = sum(1 for it in _items if it.get("status") == "carded")
                                _next_step = _m.get("next_step", "transcribe")
                                _active = tasksvc.has_active_task(bid)

                                st.markdown("---")
                                st.markdown("**Batch 状态**")
                                sc1, sc2, sc3, sc4 = st.columns(4)
                                sc1.metric("source_items", _n_total)
                                sc2.metric("已转写", _n_transcribed)
                                sc3.metric("已卡片化", _n_carded)
                                sc4.metric("next_step", _next_step)

                                st.markdown("**操作**")
                                st.caption("转写/卡片化在后台子进程运行，不阻塞页面。也可切换到「转写」/「卡片化」tab 查看完整日志。")
                                ac1, ac2 = st.columns(2)
                                with ac1:
                                    if st.button("▶ 创建并运行转写任务",
                                                 key=f"ai_transcribe_{cid}",
                                                 disabled=_active):
                                        res = tasksvc.create_and_launch(bid, "transcribe")
                                        if res.get("ok"):
                                            st.success(f"转写任务已启动 pid={res.get('pid')}")
                                        else:
                                            st.error(res.get("error"))
                                        st.rerun()
                                with ac2:
                                    if st.button("▶ 创建并运行卡片化任务",
                                                 key=f"ai_cards_{cid}",
                                                 disabled=_active):
                                        res = tasksvc.create_and_launch(bid, "build_cards")
                                        if res.get("ok"):
                                            st.success(f"卡片化任务已启动 pid={res.get('pid')}")
                                        else:
                                            st.error(res.get("error"))
                                        st.rerun()
                                if _active:
                                    st.info("已有任务运行中（v1 串行）。请到「转写」或「卡片化」tab 查看进度。")
                                st.caption(f"完成卡片化后请至「卡片审核」tab 逐条审核，不自动 approve。")

                                # ── Auto-5: 审核前安全总览 ─────────────────
                                st.markdown("---")
                                st.markdown("**审核前安全总览**")
                                try:
                                    _rv_summary = review.summary(bid)
                                    _n_pending  = _rv_summary.get("pending", 0)
                                    _n_approved = _rv_summary.get("approved", 0)
                                    _n_rejected = _rv_summary.get("rejected", 0)
                                    _n_cards_total = _rv_summary.get("total", 0)

                                    rv1, rv2, rv3, rv4, rv5, rv6, rv7 = st.columns(7)
                                    rv1.metric("source_items", _n_total)
                                    rv2.metric("已转写", _n_transcribed)
                                    rv3.metric("已卡片化", _n_carded)
                                    rv4.metric("待审核卡片", _n_pending)
                                    rv5.metric("已通过", _n_approved)
                                    rv6.metric("已拒绝", _n_rejected)
                                    rv7.metric("活跃任务", "是" if _active else "否")

                                    _all_transcribed = _n_transcribed >= _n_total > 0
                                    _all_carded = _n_carded >= _n_total > 0
                                    _no_pending = _n_cards_total > 0 and _n_pending == 0

                                    if _active or not _all_transcribed or not _all_carded:
                                        st.warning("⛔ not_ready：转写或卡片化未完成，或有活跃任务在运行，暂不可进入审核流程。")
                                    elif _n_pending > 0:
                                        st.info(f"📋 review_ready：已卡片化，{_n_pending} 张卡片待审核。请至「卡片审核」tab 逐条审核。")
                                    elif _no_pending and _n_approved > 0:
                                        st.success(f"✅ approve_candidate：全部 {_n_approved} 张卡片已审核通过，可进入 approve dry-run。")

                                        # ── Auto-6: dry-run 入口 ──────────────
                                        st.markdown("---")
                                        st.markdown("**Approve Dry-Run**")
                                        _dry_cmd = (
                                            f"python scripts/approve_mentor_ingest_batch.py"
                                            f" --batch {bid} --decision approve --dry-run"
                                        )
                                        st.code(_dry_cmd, language="bash")
                                        st.caption(
                                            f"batch_id: `{bid}` · 审核通过卡片: {_n_approved} 张  \n"
                                            "⚠ dry-run 只预检，不写正式知识库。真实 approve 需在「审核合并」tab 完成双重确认。"
                                        )
                                        _dry_active = tasksvc.has_active_task(bid)
                                        if st.button(
                                            "▶ 创建并运行 approve dry-run 任务",
                                            key=f"ai_dryrun_{cid}",
                                            disabled=_dry_active,
                                        ):
                                            _dry_res = tasksvc.create_and_launch(bid, "approve_dry_run")
                                            if _dry_res.get("ok"):
                                                st.success(f"dry-run 任务已启动 pid={_dry_res.get('pid')}")
                                            else:
                                                st.error(_dry_res.get("error"))
                                            st.rerun()
                                        if _dry_active:
                                            st.info("已有任务运行中，请到「审核合并」tab 查看 dry-run 日志。")

                                        # ── Auto-7: 真实 approve 安全入口 ────────
                                        st.markdown("---")
                                        st.markdown("**真实 Approve（写入正式知识库）**")
                                        st.error(
                                            "⚠ 以下操作将把卡片/chunk **永久写入正式知识库**。\n\n"
                                            "必须满足：① 已完成 dry-run 并确认结果；② 输入正确 batch_id；\n"
                                            "③ approve 脚本自身会执行备份与校验，不可绕过。"
                                        )
                                        _real_dry_checked = st.checkbox(
                                            "我已完成 dry-run 并确认结果",
                                            key=f"ai_realapprove_drycheck_{cid}",
                                        )
                                        _real_bid_input = st.text_input(
                                            "输入 batch_id 以确认（防误触）",
                                            value="",
                                            key=f"ai_realapprove_bid_{cid}",
                                            placeholder=bid,
                                        )
                                        _bid_confirmed = (_real_bid_input.strip() == bid)
                                        _real_approve_ready = _real_dry_checked and _bid_confirmed
                                        _real_active = tasksvc.has_active_task(bid)

                                        _real_cmd = (
                                            f"python scripts/approve_mentor_ingest_batch.py"
                                            f" --batch {bid} --decision approve"
                                        )
                                        st.code(_real_cmd, language="bash")
                                        st.caption(
                                            "任务将调用 approve 脚本（含备份 + P1 审核闸门 + 去重校验），"
                                            "不绕过任何现有保护逻辑。"
                                        )

                                        if st.button(
                                            "▶ 创建并运行真实 approve 任务",
                                            key=f"ai_realapprove_{cid}",
                                            disabled=(not _real_approve_ready or _real_active),
                                            type="primary",
                                        ):
                                            _real_res = tasksvc.create_and_launch(bid, "approve_merge")
                                            if _real_res.get("ok"):
                                                st.success(
                                                    f"✅ approve_merge 任务已启动 pid={_real_res.get('pid')}。"
                                                    "请到「审核合并」tab 查看日志。"
                                                )
                                            else:
                                                st.error(_real_res.get("error"))
                                            st.rerun()

                                        if not _real_dry_checked:
                                            st.caption('→ 请先勾选"已完成 dry-run"')
                                        elif not _bid_confirmed:
                                            st.caption(f"→ 请输入正确 batch_id：`{bid}`")
                                        if _real_active:
                                            st.info("已有任务运行中，请等待完成后再 approve。")
                                    else:
                                        st.warning("⚠ 无可审核卡片，请确认卡片化是否已完成。")

                                    st.caption("approve 必须走后续 dry-run + 备份 + 双重确认流程，当前页面不会写正式知识库。")
                                except Exception as _rv_ex:
                                    st.warning(f"⚠ 无法读取审核数据（{_rv_ex}），请确认卡片化已完成。")
                        else:
                            st.error(f"创建失败：{_br.get('error', '未知错误')}")
                            for w in _br.get("warnings", []):
                                st.caption(f"⚠ {w}")
        elif _scan.get("exists"):
            st.caption("主目录下没有可用候选文件夹。")


# ─── Tab: 导师身份(脱敏映射)───────────────────────
with tab_map["identity"]:
    st.subheader("导师身份(脱敏映射)")
    st.caption("前台仅展示 public_name + style_label;后台保留 real_mentor_name / 平台账号 / 来源课程。")

    identities = ident.list_identities()
    if identities:
        st.dataframe(
            [{
                "public_id":   i.get("public_mentor_id", ""),
                "public_name": i.get("public_name", ""),
                "style_label": i.get("style_label", ""),
                "real_mentor": i.get("real_mentor_name", ""),
                "platform":    i.get("platform", ""),
                "visibility":  i.get("visibility", "public"),
            } for i in identities],
            use_container_width=True, hide_index=True,
        )
        with st.expander("展开查看课程/描述/备注等长字段", expanded=False):
            st.dataframe(
                [{
                    "public_id":          i.get("public_mentor_id", ""),
                    "public_name":        i.get("public_name", ""),
                    "public_description": i.get("public_description", ""),
                    "aggregate_courses":  ", ".join(i.get("aggregate_courses") or []),
                    "source_notes":       i.get("source_notes", ""),
                } for i in identities],
                use_container_width=True, hide_index=True,
            )
    else:
        st.info("暂无 identity 映射,使用下方表单新增。")

    st.markdown("#### 新增 / 更新")
    with st.form("identity_form"):
        c1, c2, c3 = st.columns(3)
        with c1:
            public_name = st.text_input("public_name(前台显示)", "")
            style_label = st.text_input("style_label", "")
            visibility = st.selectbox("visibility", ["public", "hidden"], index=0)
        with c2:
            real_mentor_name = st.text_input("real_mentor_name(后台真实)", "")
            platform = st.text_input("platform", "")
            account_id = st.text_input("account_id", "")
        with c3:
            homepage_url = st.text_input("homepage_url", "")
            public_mentor_id = st.text_input("public_mentor_id(留空自动生成)", "")
            agg_text = st.text_area("aggregate_courses(每行一个)", height=80)
        public_description = st.text_area("public_description(前台简介,可选)", "")
        source_notes = st.text_area("source_notes(后台备注,可选)", "")
        submitted = st.form_submit_button("保存")
        if submitted:
            agg_list = [s.strip() for s in (agg_text or "").splitlines() if s.strip()]
            rec = ident.upsert_identity(
                public_name=public_name, style_label=style_label,
                real_mentor_name=real_mentor_name,
                public_description=public_description, visibility=visibility,
                platform=platform, homepage_url=homepage_url, account_id=account_id,
                source_notes=source_notes, aggregate_courses=agg_list,
                public_mentor_id=public_mentor_id or None,
            )
            st.success(f"已保存:{rec.get('public_mentor_id')}({rec.get('public_name')})")
            st.rerun()

    st.markdown("#### 删除")
    if identities:
        del_id = st.selectbox("选择要删除的 public_mentor_id",
                              [""] + [i["public_mentor_id"] for i in identities])
        if del_id and st.button("删除", type="secondary"):
            ok = ident.delete_identity(del_id)
            if ok:
                st.success(f"已删除 {del_id}")
                st.rerun()


# ─── Tab: 新建采集 (高级入口，SHOW_ADVANCED_INGEST_TABS=True 时可见)──────────────────────
if SHOW_ADVANCED_INGEST_TABS:
    with tab_map["new_collect"]:
        st.subheader("新建采集任务")
        settings = load_settings()
        target_mode = st.selectbox(
            "target_mode",
            ["archive_only", "new_mentor", "merge_existing_mentor", "merge_existing_course"],
            index=["archive_only", "new_mentor", "merge_existing_mentor",
                   "merge_existing_course"].index(settings["ingest"]["default_target_mode"]),
            help="archive_only=只保存素材;new_mentor=新建导师;merge_existing_mentor/course=合并到已有",
        )
        c1, c2 = st.columns(2)
        with c1:
            real_mentor = st.text_input("real_mentor_name(后台)", "")
            public_mentor_id = st.text_input("public_mentor_id(可选)", "")
            public_name = st.text_input("public_name(前台化名)", "")
        with c2:
            style_label = st.text_input("style_label", "")
            target_course = st.text_input("target_course(merge 课程时)", "")
            label = st.text_input("batch label(可选)", "")

        common_kwargs = dict(
            target_mode=target_mode,
            real_mentor_name=real_mentor,
            public_mentor_id=public_mentor_id,
            public_name=public_name,
            style_label=style_label,
            target_course=target_course,
            label=label,
        )

        sub_tabs = st.tabs(["本地文件夹", "单视频 URL", "抖音主页", "B站主页"])

        with sub_tabs[0]:
            folder = st.text_input("文件夹路径", str(RAW_LOCAL))
            st.caption("将扫描视频(mp4/mov/mkv/...) / 音频(mp3/m4a/wav/...) / 文本(txt/srt/vtt/md)。")
            if st.button("扫描并创建批次", key="scan_local"):
                try:
                    m = create_batch("local_folder", local_path=folder, **common_kwargs)
                    r = scan_local_folder(m["batch_id"], Path(folder))
                    if r.get("ok"):
                        st.success(f"batch_id = {m['batch_id']} · 新增 {r['items_added']} 项 · 跳过重复 {r['skipped_duplicates']}")
                    else:
                        st.error(r.get("error", "扫描失败"))
                except Exception as e:
                    st.error(f"创建失败: {e}")

        with sub_tabs[1]:
            url = st.text_input("视频 URL", "", key="single_url")
            if st.button("登记 URL", key="reg_url"):
                m = create_batch("single_url", source_url=url, **common_kwargs)
                r = collect_single_url(m["batch_id"], url)
                st.success(f"batch_id = {m['batch_id']} · {r}")
                st.caption("第一版仅登记 URL,不主动下载。请手动下载后用本地导入,或在 admin_settings 配置 yt_dlp_exe 后由外部工具下载到 batch videos 目录。")

        with sub_tabs[2]:
            url = st.text_input("抖音博主主页 URL", "", key="dy_url")
            st.caption("⚠ 抖音风控强,系统不绕过风控。本入口仅登记主页,需手动下载公开视频到 raw_input/local 后改走本地导入。")
            if st.button("登记抖音主页", key="reg_dy"):
                m = create_batch("douyin_homepage", source_url=url, **common_kwargs)
                r = collect_douyin_homepage(m["batch_id"], url)
                st.warning(f"已优雅 fallback:{r}")
                st.info(f"batch_id = {m['batch_id']}")

        with sub_tabs[3]:
            url = st.text_input("B站 UP 主主页 URL", "", key="bi_url")
            st.caption("可在工具配置中填写 bilibili_downloader_exe(合规外部工具),由外部工具下载公开视频到 batch videos 目录。系统不破解 DRM/付费/会员内容。")
            if st.button("登记 B 站主页", key="reg_bi"):
                m = create_batch("bilibili_homepage", source_url=url, **common_kwargs)
                r = collect_bilibili_homepage(m["batch_id"], url)
                st.warning(f"已优雅 fallback:{r}")
                st.info(f"batch_id = {m['batch_id']}")


# ─── Tab 3-7: 选 batch 后通用上下文 ───────────────────
batches = list_batches()
batch_options = [b["batch_id"] for b in batches] if batches else []


def _select_batch(key: str) -> dict | None:
    if not batch_options:
        st.info("暂无批次。")
        return None
    bid = st.selectbox("选择批次", batch_options, key=key)
    return load_manifest(bid)


# ─── 任务面板(转写/卡片化/审核合并 复用)────────────────
_STATUS_ICON = {
    "pending": "⏳", "running": "🔄", "success": "✅",
    "failed": "❌", "skipped": "⏭", "cancelled": "🚫",
}

# approve panel 展示的任务类型(审核/合并相关,不含 transcribe/build_cards 等)
_APPROVE_PANEL_TASK_TYPES = ("approve_dry_run", "approve_merge")


def _widget_key(*parts: object) -> str:
    """生成稳定且唯一的 Streamlit widget key。

    - None / 空值有稳定 fallback("_")
    - 路径分隔符、空格、中文等非 ASCII 字符统一替换为 "_",避免破坏 key
    - 超长片段(如含中文文件名的 task_id)做 hash 截断,避免 key 过长/不稳定
    """
    safe_parts: list[str] = []
    for p in parts:
        s = "" if p is None else str(p)
        # 替换路径分隔符 / 空格 / 各类空白
        s = s.replace("\\", "_").replace("/", "_")
        # 仅保留 ASCII 字母数字、下划线、连字符、点;其余(含中文、空格)→ "_"
        s = re.sub(r"[^A-Za-z0-9_.\-]", "_", s)
        # 过长片段截断并附加短 hash,保证唯一且稳定
        if len(s) > 48:
            h = hashlib.md5(s.encode("utf-8")).hexdigest()[:8]
            s = s[:40] + "_" + h
        safe_parts.append(s or "_")
    return "_".join(safe_parts) or "_"


def _render_task_panel(batch_id: str, task_type=None, panel_key: str = ""):
    """显示某 batch(可选某 task_type)的任务列表 + 日志 + retry/skip/cancel。

    panel_key 必须能区分不同调用点;所有 widget key 由 _widget_key(panel_key,
    batch_id, task_type, task_id, idx) 组成,确保同一 task 在不同 panel 下 key 不冲突。

    task_type 支持:
      - None       → 展示该 batch 全部任务
      - str        → 仅展示该类型(走 tasksvc 过滤)
      - tuple/list → 展示其中任一类型(Python 侧过滤)
    """
    if isinstance(task_type, (tuple, list)):
        allowed = set(task_type)
        all_tasks = [t for t in tasksvc.list_tasks(batch_id)
                     if t.get("task_type") in allowed]
    else:
        all_tasks = tasksvc.list_tasks(batch_id, task_type=task_type)
    summary = tasksvc.batch_task_summary(batch_id)
    cols = st.columns(6)
    for col, s in zip(cols, ("pending", "running", "success", "failed", "skipped", "cancelled")):
        col.metric(f"{_STATUS_ICON[s]} {s}", summary.get(s, 0))

    rc1, rc2 = st.columns([1, 4])
    with rc1:
        if st.button("🔄 手动刷新", key=_widget_key("refresh", panel_key, batch_id)):
            st.rerun()
    with rc2:
        if tasksvc.has_active_task(batch_id):
            st.caption("有任务运行中,点刷新查看最新进度(v1 串行,一次只跑一个)。")

    if not all_tasks:
        st.caption("暂无任务。")
        return

    for idx, t in enumerate(all_tasks[:30]):
        tid = t["task_id"]
        ttype = t.get("task_type", "")
        icon = _STATUS_ICON.get(t.get("status"), "?")
        header = (f"{icon} {t['task_type']} · {t.get('status')} · "
                  f"progress={t.get('progress', 0)} · {tid}")
        with st.expander(header, expanded=(t.get("status") == "running")):
            meta_cols = st.columns(4)
            meta_cols[0].write(f"**status**: {t.get('status')}")
            meta_cols[1].write(f"**progress**: {t.get('progress', 0)}")
            meta_cols[2].write(f"**retry**: {t.get('retry_count', 0)}/{t.get('max_retries', 0)}")
            meta_cols[3].write(f"**pid**: {t.get('pid')}")
            st.write(f"**message**: {t.get('message', '')}")
            st.write(f"**started_at**: {t.get('started_at') or '-'} · "
                     f"**finished_at**: {t.get('finished_at') or '-'}")
            if t.get("command"):
                st.code(t["command"], language="bash")
            if t.get("error"):
                st.error(t["error"][:2000])

            log_tail = tasksvc.read_log_tail(batch_id, tid, max_lines=120)
            if log_tail:
                st.text_area("log tail", log_tail, height=180,
                             key=_widget_key("log", panel_key, batch_id, ttype, tid, idx))

            ac1, ac2, ac3 = st.columns(3)
            with ac1:
                if st.button("retry", key=_widget_key("retry", panel_key, batch_id, ttype, tid, idx),
                             disabled=t.get("status") not in ("failed", "cancelled", "skipped")):
                    tasksvc.retry_task(batch_id, tid)
                    res = tasksvc.launch_task(batch_id, tid)
                    if not res.get("ok"):
                        st.warning(res.get("error"))
                    st.rerun()
            with ac2:
                if st.button("skip", key=_widget_key("skip", panel_key, batch_id, ttype, tid, idx),
                             disabled=t.get("status") in ("success", "skipped")):
                    tasksvc.skip_task(batch_id, tid)
                    st.rerun()
            with ac3:
                if st.button("cancel", key=_widget_key("cancel", panel_key, batch_id, ttype, tid, idx),
                             disabled=t.get("status") not in ("pending", "running")):
                    tasksvc.cancel_task(batch_id, tid)
                    st.rerun()


def _render_p2a_items(batch_id: str):
    """展示 P2A source_items + 逐项下载/重试(下载只走任务队列,绝不同步)。"""
    items = load_source_items(batch_id)
    p2a = [it for it in items if it.get("platform") in ("bilibili", "url")
           and it.get("status") in ("metadata_ready", "failed", "downloaded", "skipped")]
    st.markdown(f"**本 batch P2A 采集条目:{len(p2a)} 项**")
    if not p2a:
        st.caption("暂无 metadata 条目。抓取成功后会显示 title / author / 时长 / BV / status。")
        return

    active = tasksvc.has_active_task(batch_id)
    for it in p2a:
        iid = it.get("item_id")
        status = it.get("status")
        icon = {"metadata_ready": "📄", "downloaded": "✅",
                "failed": "❌", "skipped": "⏭"}.get(status, "?")
        with st.expander(f"{icon} {iid} · {status} · {(it.get('title') or '')[:40]}",
                         expanded=False):
            cc = st.columns(3)
            cc[0].caption(f"platform: {it.get('platform')}")
            cc[1].caption(f"BV/id: {it.get('platform_item_id','')}")
            cc[2].caption(f"duration: {it.get('duration_sec',0)}s")
            st.caption(f"author: {it.get('author_name','')} · published: {it.get('published_at','')}")
            if it.get("source_url"):
                st.write(f"source_url: {it['source_url']}")
            if it.get("cover_url"):
                st.caption(f"cover_url: {it['cover_url'][:80]}")

            if status == "downloaded":
                st.success(f"local_video_path: {it.get('local_video_path','')}")
                st.code(it.get("file_hash", ""), language="text")
            if status == "skipped" and isinstance(it.get("error"), dict):
                st.warning(f"skipped · {it['error'].get('code','')} · {it['error'].get('message','')}")
            if status == "failed" and isinstance(it.get("error"), dict):
                e = it["error"]
                st.error(f"code={e.get('code','')} · {e.get('message','')}")

            bc = st.columns(2)
            with bc[0]:
                if status in ("metadata_ready", "downloaded", "skipped"):
                    force = status in ("downloaded", "skipped")
                    label = "重新下载视频(force)" if force else "下载视频"
                    if st.button(label, key=f"p2a_dl_{batch_id}_{iid}", disabled=active):
                        res = tasksvc.create_and_launch(
                            batch_id, "download_video", item_id=iid,
                            extra={"item_id": iid,
                                   "quality": "best[height<=720]/best",
                                   "force": force})
                        if res.get("ok"):
                            st.success(f"已启动下载任务 pid={res.get('pid')}")
                        else:
                            st.error(res.get("error"))
                        st.rerun()
            with bc[1]:
                if status == "failed":
                    if st.button("重试 metadata", key=f"p2a_remeta_{batch_id}_{iid}",
                                 disabled=(active or not it.get("source_url"))):
                        res = tasksvc.create_and_launch(
                            batch_id, "collect_url_metadata",
                            extra={"url": it.get("source_url", "")})
                        if res.get("ok"):
                            st.success(f"已启动 metadata 重抓任务 pid={res.get('pid')}")
                        else:
                            st.error(res.get("error"))
                        st.rerun()
    if active:
        st.info("已有任务运行中(v1 串行)。下载在后台进程执行,点任务面板刷新查看进度。")


def _render_candidate_picker(batch_id: str, report: dict, active: bool):
    """渲染候选列表 + 多选 + 导入/下载按钮。"""
    cands = report.get("items", []) or []
    if not cands:
        return

    def _ckey(cid: str) -> str:
        return f"cand_{batch_id}_{cid}"

    pick_cols = st.columns(2)
    with pick_cols[0]:
        if st.button("全选未导入", key=f"sel_all_{batch_id}"):
            for c in cands:
                if c.get("status") != "imported":
                    st.session_state[_ckey(c["candidate_id"])] = True
            st.rerun()
    with pick_cols[1]:
        if st.button("清空选择", key=f"sel_none_{batch_id}"):
            for c in cands:
                st.session_state[_ckey(c["candidate_id"])] = False
            st.rerun()

    new_selected = set()
    for c in cands:
        cid = c["candidate_id"]
        imported = c.get("status") == "imported"
        label = (f"{'✅导入' if imported else '⬜'} {c.get('platform_item_id','')} · "
                 f"{(c.get('title') or '')[:36]} · {c.get('duration_sec',0)}s · "
                 f"{c.get('author_name','')}")
        checked = st.checkbox(label, key=_ckey(cid), disabled=imported)
        if checked and not imported:
            new_selected.add(cid)
        if c.get("source_url"):
            st.caption(f"　{c['source_url']}　"
                       + (f"→ {c.get('imported_item_id','')}" if imported else ""))

    st.markdown("---")
    bc = st.columns(3)
    with bc[0]:
        if st.button("① 导入选中候选到 source_items", key=f"imp_{batch_id}",
                     disabled=not new_selected):
            r = collect.import_report_candidates(batch_id, list(new_selected))
            st.success(f"导入 {r['imported_count']} · 跳过重复 {r['skipped_duplicate_count']}")
            st.rerun()
    with bc[1]:
        if st.button("② 为已导入候选创建下载任务", key=f"dlimp_{batch_id}",
                     disabled=active):
            n = _enqueue_downloads_for_imported(batch_id, report)
            st.success(f"已创建 {n} 个下载任务(v1 串行,逐个执行)")
            st.rerun()
    with bc[2]:
        if st.button("③ 导入并下载选中候选", key=f"impdl_{batch_id}",
                     disabled=active or not new_selected):
            r = collect.import_report_candidates(batch_id, list(new_selected))
            n = _enqueue_downloads_for_item_ids(batch_id, r.get("item_ids", []))
            st.success(f"导入 {r['imported_count']} · 跳过 {r['skipped_duplicate_count']} · "
                       f"创建下载任务 {n}")
            st.rerun()
    if active:
        st.info("已有任务运行中(v1 串行)。下载在后台逐个执行,点任务面板刷新。")
    n_pending_dl = len(tasksvc.list_tasks(batch_id, status="pending",
                                          task_type="download_video"))
    if st.button(f"▶ 启动下一个待下载任务(待执行 {n_pending_dl})",
                 key=f"kick_dl_{batch_id}", disabled=active or n_pending_dl == 0):
        _kick_pending_download(batch_id)
        st.rerun()
    st.caption("v1 串行:一次只跑一个下载。批量下载会创建多个 pending 任务,完成一个后点此启动下一个。")
    st.markdown("#### 视频下载任务")
    _render_task_panel(batch_id, task_type="download_video",
                       panel_key=f"space_dl_{batch_id}")


def _enqueue_downloads_for_item_ids(batch_id: str, item_ids: list[str]) -> int:
    """为给定 item_ids 中尚未下载的项各建一个 download_video 任务(pending)。"""
    items = load_source_items(batch_id)
    by_id = {it.get("item_id"): it for it in items}
    n = 0
    for iid in item_ids:
        it = by_id.get(iid)
        if not it or it.get("status") == "downloaded":
            continue
        tasksvc.create_task(batch_id, "download_video", item_id=iid,
                            extra={"item_id": iid,
                                   "quality": "best[height<=720]/best"})
        n += 1
    if n:
        _kick_pending_download(batch_id)
    return n


def _enqueue_downloads_for_imported(batch_id: str, report: dict) -> int:
    ids = [c.get("imported_item_id") for c in report.get("items", [])
           if c.get("status") == "imported" and c.get("imported_item_id")]
    return _enqueue_downloads_for_item_ids(batch_id, ids)


def _kick_pending_download(batch_id: str):
    """v1 串行:若当前无 running 任务,启动最早的一个 pending download_video。"""
    if tasksvc.has_active_task(batch_id):
        return
    pend = [t for t in tasksvc.list_tasks(batch_id, status="pending",
                                          task_type="download_video")]
    if pend:
        pend.sort(key=lambda t: t.get("created_at", ""))
        tasksvc.launch_task(batch_id, pend[0]["task_id"])


def _render_bilibili_space(batch_id: str):
    """P2A-4/5:B站空间候选枚举 + 选择导入 + 批量下载(下载只走任务队列)。"""
    settings = load_settings()
    default_max = int(settings.get("ingest", {}).get("bilibili_max_items",
                                                     collect.DEFAULT_MAX_ITEMS))
    active = tasksvc.has_active_task(batch_id)

    sc1, sc2 = st.columns([3, 1])
    with sc1:
        space_url = st.text_input("B站 UP 主主页/空间 URL(space.bilibili.com/<mid>)",
                                  "", key=f"space_url_{batch_id}")
    with sc2:
        max_items = st.number_input("max_items", min_value=1,
                                    max_value=collect.HARD_MAX_ITEMS,
                                    value=default_max, step=1,
                                    key=f"space_max_{batch_id}")
    st.caption("仅枚举公开视频浅 metadata,不下载;不使用 cookies、不绕过风控、不枚举会员/付费/私密内容。")

    if st.button("枚举候选列表(任务队列后台执行)", key=f"space_run_{batch_id}",
                 disabled=active or not space_url.strip()):
        res = tasksvc.create_and_launch(
            batch_id, "collect_bilibili_space",
            extra={"space_url": space_url.strip(), "max_items": int(max_items)})
        if res.get("ok"):
            st.success(f"已启动空间枚举任务 pid={res.get('pid')}")
        else:
            st.error(res.get("error"))
        st.rerun()

    report = collect.load_collect_report(batch_id)
    if not report:
        st.caption("暂无 collect_report.json。先枚举候选列表。")
        _render_task_panel(batch_id, task_type="collect_bilibili_space",
                           panel_key=f"space_task_{batch_id}")
        return

    if report.get("status") == "failed":
        e = report.get("error") or {}
        st.error(f"枚举失败 · code={e.get('code','') if isinstance(e, dict) else ''} · "
                 f"{e.get('message','') if isinstance(e, dict) else e}")
        st.caption("兜底:可改用「新建采集 → 本地文件夹」导入手动收集的公开视频;"
                   "不绕过平台风控、不处理私密/会员/付费内容。")
    else:
        st.success(f"候选 {len(report.get('items', []))} 项 · total_seen={report.get('total_seen',0)} · "
                   f"truncated={report.get('truncated', False)} · author={report.get('author_name','')}")
    _render_candidate_picker(batch_id, report, active)
    st.markdown("#### 空间枚举任务")
    _render_task_panel(batch_id, task_type="collect_bilibili_space",
                       panel_key=f"space_task_{batch_id}")
# ─── Tab: 扫描报告 (高级入口，SHOW_ADVANCED_INGEST_TABS=True 时可见)──────────────────────
if SHOW_ADVANCED_INGEST_TABS:
    with tab_map["scan_report"]:
        st.subheader("扫描 / 采集报告")
        m = _select_batch("scan_batch_select")
        if m:
            st.json({k: v for k, v in m.items() if k != "status_history"})
            items = load_source_items(m["batch_id"])
            st.markdown(f"**source_items 共 {len(items)} 项**")
            if items:
                st.dataframe([{
                    "item_id": it["item_id"], "platform": it.get("platform"),
                    "kind": it.get("kind"), "status": it.get("status"),
                    "title": (it.get("title") or "")[:30],
                    "src": it.get("local_video_path") or it.get("local_audio_path") or it.get("transcript_path") or it.get("source_url"),
                } for it in items], use_container_width=True, hide_index=True)
            report = batch_dir(m["batch_id"]) / "ingest_report.json"
            if report.exists():
                with st.expander("ingest_report.json"):
                    st.json(json.loads(report.read_text(encoding="utf-8")))


# ─── Tab: 转写 ──────────────────────────────────────
with tab_map["transcribe"]:
    st.subheader("转写")
    m = _select_batch("trans_batch_select")
    if m:
        items = load_source_items(m["batch_id"])
        n_pending = sum(1 for it in items
                        if it.get("kind") in ("video", "audio")
                        and it.get("status") in ("downloaded", "audio_extracted"))
        n_done = sum(1 for it in items if it.get("status") == "transcribed")
        c1, c2, c3 = st.columns(3)
        c1.metric("待转写", n_pending)
        c2.metric("已转写", n_done)
        c3.metric("总数", len(items))
        st.code(f"python scripts/transcribe_videos.py --batch {m['batch_id']}", language="bash")
        st.caption("ASR 通常较慢,建议在终端运行。脚本写回 source_items.json,本页刷新即可看到 transcript_path。")

        st.markdown("---")
        st.markdown("#### 任务队列执行(UI 内后台运行)")
        active = tasksvc.has_active_task(m["batch_id"])
        bc1, bc2 = st.columns(2)
        with bc1:
            if st.button("▶ 创建并运行转写任务", key="run_transcribe", disabled=active):
                res = tasksvc.create_and_launch(m["batch_id"], "transcribe")
                if res.get("ok"):
                    st.success(f"已启动转写任务 pid={res.get('pid')}")
                else:
                    st.error(res.get("error"))
                st.rerun()
        with bc2:
            if st.button("↻ 重试失败转写任务", key="retry_transcribe", disabled=active):
                res = tasksvc.create_and_launch(m["batch_id"], "transcribe",
                                                extra={"retry_failed": True})
                if res.get("ok"):
                    st.success(f"已启动重试任务 pid={res.get('pid')}")
                else:
                    st.error(res.get("error"))
                st.rerun()
        if active:
            st.info("已有任务运行中(v1 串行)。点下方刷新查看进度。")
        _render_task_panel(m["batch_id"], task_type="transcribe", panel_key="transcribe")
# ─── Tab: 卡片化 ────────────────────────────────────
with tab_map["build_cards"]:
    st.subheader("卡片化")
    m = _select_batch("card_batch_select")
    if m:
        bd = batch_dir(m["batch_id"])
        chunks_path = bd / "chunks.json"
        cards_path = bd / "draft_cards.json"
        st.write(f"chunks.json: {'已生成' if chunks_path.exists() else '未生成'}")
        st.write(f"draft_cards.json: {'已生成' if cards_path.exists() else '未生成'}")
        st.code(
            f"python scripts/build_ingest_cards.py --batch {m['batch_id']} --skip-cards   # 仅清洗+切块\n"
            f"python scripts/build_ingest_cards.py --batch {m['batch_id']} --sample 10    # 卡片化前 10 个\n"
            f"python scripts/build_ingest_cards.py --batch {m['batch_id']} --resume       # 全量+续跑",
            language="bash",
        )

        st.markdown("---")
        st.markdown("#### 任务队列执行(UI 内后台运行)")
        st.caption("卡片化会调用 DeepSeek API(由 build_ingest_cards.py 自身发起)。任务在后台进程运行,本页不阻塞。")
        active = tasksvc.has_active_task(m["batch_id"])
        oc1, oc2, oc3 = st.columns(3)
        with oc1:
            skip_cards = st.checkbox("仅清洗+切块(--skip-cards)", key="bc_skip")
        with oc2:
            resume = st.checkbox("续跑(--resume)", key="bc_resume")
        with oc3:
            sample = st.number_input("sample(0=全量)", min_value=0, value=0, step=1, key="bc_sample")
        if st.button("▶ 创建并运行卡片化任务", key="run_cards", disabled=active):
            extra = {"skip_cards": skip_cards, "resume": resume}
            if sample and sample > 0:
                extra["sample"] = int(sample)
            res = tasksvc.create_and_launch(m["batch_id"], "build_cards", extra=extra)
            if res.get("ok"):
                st.success(f"已启动卡片化任务 pid={res.get('pid')}")
            else:
                st.error(res.get("error"))
            st.rerun()
        if active:
            st.info("已有任务运行中(v1 串行)。点下方刷新查看进度。")
        _render_task_panel(m["batch_id"], task_type="build_cards", panel_key="build_cards")
# ─── 卡片审核闸门(P1)渲染 ───────────────────────────────
_DECISION_ICON = {
    "pending": "⏳", "approved": "✅", "rejected": "❌", "needs_regenerate": "🔁",
}


def _render_one_card(batch_id: str, row: dict):
    cid = row["card_id"]
    eff = row["effective"]
    st.markdown(f"**card_id**: `{cid}` · **当前状态**: "
                f"{_DECISION_ICON.get(row['decision'],'?')} {row['decision']}"
                + (" · ✏ 已编辑" if row["edited"] else ""))

    meta = st.columns(4)
    meta[0].caption(f"card_type: {eff.get('card_type','')}")
    meta[1].caption(f"relevance: {eff.get('relevance_score','')}")
    meta[2].caption(f"noise: {eff.get('noise_score','')}")
    meta[3].caption(f"safety: {eff.get('safety_level','')}")

    k = f"{batch_id}_{cid}"
    concept = st.text_input("concept", eff.get("concept", ""), key=f"c_concept_{k}")
    core_claim = st.text_area("core_claim", eff.get("core_claim", ""), height=80, key=f"c_claim_{k}")
    actionable = st.text_area("actionable_moves(每行一条)",
                              review.list_to_lines(eff.get("actionable_moves")), height=110, key=f"c_act_{k}")
    mistakes = st.text_area("common_mistakes(每行一条)",
                            review.list_to_lines(eff.get("common_mistakes")), height=90, key=f"c_mis_{k}")
    risks = st.text_area("risk_notes(每行一条)",
                         review.list_to_lines(eff.get("risk_notes")), height=80, key=f"c_risk_{k}")
    tags = st.text_area("tags(每行一个)",
                        review.list_to_lines(eff.get("tags")), height=70, key=f"c_tags_{k}")
    include = st.checkbox("include_in_tutor", value=bool(eff.get("include_in_tutor", True)), key=f"c_inc_{k}")
    notes = st.text_input("review_notes(reject / regenerate 时建议填写)",
                          (row["entry"] or {}).get("review_notes", ""), key=f"c_notes_{k}")

    # 只读展示其余字段
    with st.expander("只读字段(applicable_scenarios / example_lines / raw_excerpt 等)"):
        st.write("applicable_scenarios:", eff.get("applicable_scenarios"))
        st.write("example_lines:", eff.get("example_lines"))
        st.write("quality_flags:", eff.get("quality_flags"))
        st.caption(f"mentor={eff.get('mentor','')} · source_course={eff.get('source_course','')} "
                   f"· source_file={eff.get('source_file','')}")
        st.text_area("raw_excerpt", eff.get("raw_excerpt", ""), height=120,
                     disabled=True, key=f"c_raw_{k}")

    def _form_values():
        return {
            "concept": concept, "core_claim": core_claim,
            "actionable_moves": actionable, "common_mistakes": mistakes,
            "risk_notes": risks, "tags": tags, "include_in_tutor": include,
        }

    b = st.columns(5)
    if b[0].button("✅ approve", key=f"b_appr_{k}"):
        edited = review.build_edited_card(row["draft"], row["entry"], _form_values())
        review.set_decision(batch_id, cid, "approved", edited_card=edited, review_notes=notes)
        st.rerun()
    if b[1].button("💾 仅保存编辑", key=f"b_save_{k}"):
        edited = review.build_edited_card(row["draft"], row["entry"], _form_values())
        review.set_decision(batch_id, cid, row["decision"], edited_card=edited, review_notes=notes)
        st.rerun()
    if b[2].button("❌ reject", key=f"b_rej_{k}"):
        edited = review.build_edited_card(row["draft"], row["entry"], _form_values())
        review.set_decision(batch_id, cid, "rejected", edited_card=edited, review_notes=notes)
        st.rerun()
    if b[3].button("🔁 regenerate", key=f"b_regen_{k}"):
        review.set_decision(batch_id, cid, "needs_regenerate", review_notes=notes)
        st.rerun()
    if b[4].button("↩ 重置本卡", key=f"b_reset_{k}"):
        review.reset_card(batch_id, cid)
        st.rerun()


def _render_card_review(batch_id: str):
    rows = review.list_review_rows(batch_id)
    if not rows:
        st.info("该 batch 暂无 draft_cards。请先在「卡片化」生成 draft_cards.json。")
        return

    summ = review.summary(batch_id)
    sc = st.columns(5)
    sc[0].metric("总数", summ.get("total", 0))
    sc[1].metric("⏳ pending", summ.get("pending", 0))
    sc[2].metric("✅ approved", summ.get("approved", 0))
    sc[3].metric("❌ rejected", summ.get("rejected", 0))
    sc[4].metric("🔁 regen", summ.get("needs_regenerate", 0))

    # 过滤 + 选卡(避免一次渲染过多卡)
    fcol1, fcol2 = st.columns([1, 2])
    with fcol1:
        flt = st.selectbox("过滤 decision", ["全部"] + list(review.DECISIONS),
                           key=f"rv_filter_{batch_id}")
    view_rows = rows if flt == "全部" else [r for r in rows if r["decision"] == flt]
    if not view_rows:
        st.caption("当前过滤条件下没有卡片。")
    with fcol2:
        labels = [f"{_DECISION_ICON.get(r['decision'],'?')} {r['card_id']} · "
                  f"{(r['effective'].get('concept') or '')[:24]}" for r in view_rows] or ["—"]
        idx = st.selectbox("选择卡片", range(len(view_rows)) if view_rows else [0],
                           format_func=lambda i: labels[i], key=f"rv_pick_{batch_id}") if view_rows else None

    if view_rows and idx is not None:
        _render_one_card(batch_id, view_rows[idx])

    st.markdown("---")
    if st.button("📤 生成 approved_cards.json + rejected_cards_reviewed.json",
                 key=f"rv_export_{batch_id}", type="primary"):
        res = review.export_review_outputs(batch_id)
        st.success(f"已导出:approved={res['approved']} · rejected={res['rejected']} · "
                   f"pending={res['pending']} · regen={res['needs_regenerate']}")
        st.caption(f"approved_cards.json → {res['approved_path']}")
        st.caption("现在可去「审核合并」tab 跑 approve dry-run / 真实合并。")


# ─── Tab: 卡片审核(P1 闸门)────────────────────────
with tab_map["card_review"]:
    st.subheader("卡片逐条审核(P1 闸门)")
    st.caption("逐条 approve / reject / regenerate,导出 approved_cards.json。"
               "审核只写 batch 本地文件,不触碰正式知识库。")
    m = _select_batch("review_batch_select")
    if m:
        _render_card_review(m["batch_id"])


# ─── Tab: 审核合并 ──────────────────────────────────
with tab_map["approve"]:
    st.subheader("审核合并")
    m = _select_batch("approve_batch_select")
    if m:
        bd = batch_dir(m["batch_id"])
        cards_path = bd / "draft_cards.json"
        approved_path = bd / "approved_cards.json"
        if approved_path.exists():
            try:
                _appr = json.loads(approved_path.read_text(encoding="utf-8"))
                _appr_n = len(_appr) if isinstance(_appr, list) else 0
            except Exception:
                _appr_n = 0
            st.success(f"已存在 approved_cards.json:{_appr_n} 张已审核卡片,approve 将只合并这些卡。")
        else:
            st.warning("未找到 approved_cards.json。请先在「卡片审核」tab 逐条审核并导出,"
                       "否则 approve(含 dry-run)会被审核闸门拒绝。")
        if cards_path.exists():
            try:
                draft = json.loads(cards_path.read_text(encoding="utf-8"))
            except Exception:
                draft = []
            st.write(f"draft_cards: {len(draft)} 张")
            if draft:
                st.dataframe([{
                    "concept": c.get("concept", "")[:30],
                    "card_type": c.get("card_type"),
                    "rel": c.get("relevance_score"),
                    "noise": c.get("noise_score"),
                    "safety": c.get("safety_level"),
                    "include": c.get("include_in_tutor"),
                } for c in draft[:50]], use_container_width=True, hide_index=True)
        st.code(
            f"python scripts/approve_mentor_ingest_batch.py --batch {m['batch_id']} --decision approve --dry-run\n"
            f"python scripts/approve_mentor_ingest_batch.py --batch {m['batch_id']} --decision approve\n"
            f"python scripts/approve_mentor_ingest_batch.py --batch {m['batch_id']} --decision approve --allow-unreviewed  # 应急:跳过审核闸门\n"
            f"python scripts/approve_mentor_ingest_batch.py --batch {m['batch_id']} --decision reject --note '...'\n"
            f"python scripts/approve_mentor_ingest_batch.py --batch {m['batch_id']} --decision archive",
            language="bash",
        )
        st.caption("合并前会自动备份正式 cards/chunks/profiles 到 data/mentor_ingest/logs/backup_<ts>/。批准后建议跑 generate_mentor_profiles.py --force 刷新画像。")

        st.markdown("---")
        st.markdown("#### 任务队列执行(UI 内后台运行)")
        active = tasksvc.has_active_task(m["batch_id"])

        st.markdown("**① Dry-run 合并预览(安全,不写正式库)**")
        if st.button("▶ 运行 approve dry-run", key="run_approve_dry", disabled=active):
            res = tasksvc.create_and_launch(m["batch_id"], "approve_dry_run")
            if res.get("ok"):
                st.success(f"已启动 dry-run 任务 pid={res.get('pid')}")
            else:
                st.error(res.get("error"))
            st.rerun()

        st.markdown("**② 真实合并到正式库(高风险 · 双重确认)**")
        st.warning(
            "真实合并会把 draft_cards 写入正式 data/mentor_knowledge/cards.json + chunks.json。"
            "请先跑 dry-run 确认预览结果。此操作需双重确认。"
        )
        confirm_chk = st.checkbox("我已运行 dry-run 并确认要真实合并", key="merge_confirm_chk")
        typed_bid = st.text_input("再次输入完整 batch_id 以解锁合并按钮", key="merge_confirm_bid")
        merge_unlocked = confirm_chk and typed_bid.strip() == m["batch_id"]
        if st.button("⚠ 执行真实合并(approve_merge)", key="run_approve_merge",
                     type="primary", disabled=(active or not merge_unlocked)):
            res = tasksvc.create_and_launch(m["batch_id"], "approve_merge",
                                            extra={"confirmed": True})
            if res.get("ok"):
                st.success(f"已启动真实合并任务 pid={res.get('pid')}")
            else:
                st.error(res.get("error"))
            st.rerun()
        if not merge_unlocked:
            st.caption("需勾选确认 + 输入正确 batch_id 才能解锁真实合并按钮。")

        if active:
            st.info("已有任务运行中(v1 串行)。点下方刷新查看进度。")
        st.caption("本面板仅展示 approve dry-run / 真实合并任务。转写/卡片化任务请到对应 tab 查看。")
        _render_task_panel(m["batch_id"], task_type=_APPROVE_PANEL_TASK_TYPES,
                           panel_key="approve")
# ─── Tab: 批次历史 ──────────────────────────────────
with tab_map["history"]:
    st.subheader("批次历史")
    if not batches:
        st.info("暂无批次。")
    else:
        st.dataframe([{
            "batch_id": b["batch_id"],
            "source_type": b.get("source_type"),
            "target_mode": b.get("target_mode"),
            "status": b.get("status"),
            "real_mentor": b.get("real_mentor_name"),
            "public_name": b.get("public_name"),
            "items": b.get("stats", {}).get("item_count"),
            "transcribed": b.get("stats", {}).get("transcribed"),
            "carded": b.get("stats", {}).get("carded"),
            "created_at": b.get("created_at"),
        } for b in batches], use_container_width=True, hide_index=True)

        st.markdown("---")
        st.markdown("#### 任务队列汇总(全局轻索引)")
        if st.button("重建任务索引", key="rebuild_index"):
            tasksvc.rebuild_tasks_index()
            st.rerun()
        index = tasksvc.load_tasks_index()
        idx_batches = index.get("batches", {})
        if idx_batches:
            st.dataframe([{
                "batch_id": bid,
                "total": info.get("summary", {}).get("total", 0),
                "pending": info.get("summary", {}).get("pending", 0),
                "running": info.get("summary", {}).get("running", 0),
                "success": info.get("summary", {}).get("success", 0),
                "failed": info.get("summary", {}).get("failed", 0),
                "cancelled": info.get("summary", {}).get("cancelled", 0),
                "last_updated": info.get("last_updated", ""),
            } for bid, info in idx_batches.items()],
                use_container_width=True, hide_index=True)
        else:
            st.caption("暂无任务记录。在转写/卡片化/审核合并 tab 创建任务后这里会显示汇总。")


# ─── Tab: 工具配置 ──────────────────────────────────
with tab_map["config"]:
    st.subheader("工具配置")
    settings = load_settings()
    with st.form("settings_form"):
        c1, c2 = st.columns(2)
        with c1:
            ffmpeg_exe = st.text_input("ffmpeg_exe", settings["tools"]["ffmpeg_exe"])
            asr_backend = st.selectbox(
                "asr_backend",
                options=["openai_whisper", "whisper_cpp"],
                index=0 if (settings["tools"].get("asr_backend") or "openai_whisper") == "openai_whisper" else 1,
                help="openai_whisper=PyTorch CLI(纯 CPU,慢,fallback);whisper_cpp=whisper.cpp(AMD/NVIDIA GPU 通用,Vulkan 后端)",
            )
            whisper_exe = st.text_input("whisper_exe(openai_whisper)", settings["tools"]["whisper_exe"])
            whisper_model = st.text_input("whisper_model(openai_whisper)", settings["tools"]["whisper_model"])
            whisper_language = st.text_input("whisper_language(openai_whisper)", settings["tools"]["whisper_language"])
        with c2:
            whisper_cpp_exe = st.text_input(
                "whisper_cpp_exe", settings["tools"].get("whisper_cpp_exe", ""),
                help="例如 D:/tools/whisper.cpp/whisper-cli.exe(Windows Vulkan release)",
            )
            whisper_cpp_model = st.text_input(
                "whisper_cpp_model", settings["tools"].get("whisper_cpp_model", ""),
                help="例如 D:/tools/whisper.cpp/models/ggml-small.bin",
            )
            whisper_cpp_language = st.text_input(
                "whisper_cpp_language", settings["tools"].get("whisper_cpp_language", "zh"),
            )
            yt_dlp_exe = st.text_input("yt_dlp_exe(可选,合规)", settings["tools"].get("yt_dlp_exe", ""))
            bilibili_downloader_exe = st.text_input("bilibili_downloader_exe(可选,合规外部工具)",
                                                    settings["tools"].get("bilibili_downloader_exe", ""))
            douyin_enabled = st.checkbox("启用抖音采集器(默认关闭)",
                                         value=settings["policy"].get("douyin_collector_enabled", False))
            bili_enabled = st.checkbox("启用 B 站采集器(默认关闭)",
                                       value=settings["policy"].get("bilibili_collector_enabled", False))
        st.caption(f"cookies_file(预留):{settings['tools'].get('cookies_file', '') or '(空)'}　"
                   "当前 P2A 默认不使用 cookies、不绕过平台风控,此项保留供未来合规授权场景,暂不生效。")
        if st.form_submit_button("保存"):
            settings["tools"].update({
                "ffmpeg_exe": ffmpeg_exe,
                "asr_backend": asr_backend,
                "whisper_exe": whisper_exe,
                "whisper_model": whisper_model, "whisper_language": whisper_language,
                "whisper_cpp_exe": whisper_cpp_exe,
                "whisper_cpp_model": whisper_cpp_model,
                "whisper_cpp_language": whisper_cpp_language,
                "yt_dlp_exe": yt_dlp_exe,
                "bilibili_downloader_exe": bilibili_downloader_exe,
            })
            settings["policy"].update({
                "douyin_collector_enabled": douyin_enabled,
                "bilibili_collector_enabled": bili_enabled,
            })
            save_settings(settings)
            st.success("已保存到 data/mentor_ingest/admin_config/admin_settings.json")


# ─── Tab: URL 采集(P2A · 实验入口，SHOW_EXPERIMENTAL_P2A=True 时可见)──────
if SHOW_EXPERIMENTAL_P2A:
    with tab_map["p2a"]:
        st.subheader("URL 采集(P2A · 单 URL / B站空间)")
        st.caption("P2A-1 抓 metadata;P2A-2 单项下载;P2A-4 B站空间候选枚举;P2A-5 选择导入 + 批量下载。"
                   "下载/枚举只走任务队列(后台进程)。仅公开/授权内容,不用 cookies、不绕过风控、"
                   "不破解 DRM、不下载会员/付费/私密内容。封面只保留 cover_url,不下载。")

        _yt_base, _yt_ver = collect.resolve_yt_dlp_base()
        if _yt_base is None:
            st.warning(f"未检测到可用的 yt-dlp:{_yt_ver}　"
                       "可在「工具配置」填写 yt_dlp_exe,或 pip 安装 yt-dlp。"
                       "抓取失败时可改用「新建采集 → 本地文件夹」兜底。")
        else:
            st.success(f"yt-dlp 可用:{_yt_ver}（调用方式:{' '.join(_yt_base)}）")

        m = _select_batch("p2a_batch_select")
        if m:
            bid = m["batch_id"]
            p2a_sub = st.tabs(["单 URL（metadata + 下载）", "B站主页/空间（候选列表）"])

            with p2a_sub[0]:
                url = st.text_input("视频 URL（B站 BV/av/短链 或其它 yt-dlp 支持的单视频）",
                                    "", key="p2a_url")
                if url.strip():
                    st.caption(f"识别平台：{collect.detect_platform(url)} · "
                               f"platform_item_id：{collect.extract_platform_item_id(url) or '(待 yt-dlp 解析)'}")

                active = tasksvc.has_active_task(bid)
                c1, c2 = st.columns(2)
                with c1:
                    if st.button("▶ 抓取 metadata（任务队列后台执行）",
                                 key="p2a_run_task", disabled=active or not url.strip()):
                        res = tasksvc.create_and_launch(bid, "collect_url_metadata",
                                                        extra={"url": url.strip()})
                        if res.get("ok"):
                            st.success(f"已启动 metadata 抓取任务 pid={res.get('pid')}")
                        else:
                            st.error(res.get("error"))
                        st.rerun()
                with c2:
                    if st.button("⚡ 同步抓取（不走队列,立即返回）",
                                 key="p2a_run_sync", disabled=not url.strip()):
                        r = collect.collect_url_metadata(bid, url.strip())
                        if r.get("ok"):
                            st.success(f"metadata_ready · item_id={r['item_id']} · "
                                       f"{r.get('title','')}")
                        else:
                            _e = r.get("error") or {}
                            st.error(f"failed · code={_e.get('code','') if isinstance(_e, dict) else ''} · "
                                     f"{_e.get('message','') if isinstance(_e, dict) else r.get('error','')}")
                            st.caption("兜底:可改用「新建采集 → 本地文件夹」导入手动收集的公开视频;"
                                       "不绕过平台风控、不处理私密/会员/付费内容。")
                        st.rerun()
                if active:
                    st.info("已有任务运行中(v1 串行)。点下方刷新查看进度。")

                _render_p2a_items(bid)
                st.markdown("---")
                st.markdown("#### metadata 抓取任务")
                _render_task_panel(bid, task_type="collect_url_metadata", panel_key="p2a_meta")
                st.markdown("#### 视频下载任务")
                _render_task_panel(bid, task_type="download_video", panel_key="p2a_dl")

            with p2a_sub[1]:
                _render_bilibili_space(bid)


# ─── Tab: 平台素材下载(P4D · SHOW_PLATFORM_DL=True 时可见)──────────────────
if SHOW_PLATFORM_DL:
    with tab_map["platform_dl"]:
        st.subheader("平台素材下载")
        st.caption(
            "输入 B站空间 / YouTube 频道 / 抖音主页 URL，先扫描公开视频列表，确认后再批量下载。"
            "下载归档至 auto_ingest_root，后续在「自动入库」tab 手动扫描入库。"
            "不触发转写/卡片化/approve，不写正式知识库。"
        )

        # ── yt-dlp 可用性检查 ──────────────────────────────
        _pd_settings = load_settings()
        _pd_base, _pd_ver = pldl.resolve_yt_dlp_base(_pd_settings)
        if _pd_base is None:
            st.warning(
                f"未检测到可用的 yt-dlp：{_pd_ver}　"
                "请在「工具配置」填写 yt_dlp_exe，或执行 pip install yt-dlp。  \n"
                "**B站扫描不受影响**（走公开 API）。YouTube / 抖音扫描及所有平台下载需要 yt-dlp。"
            )
        else:
            st.success(f"yt-dlp 可用：{_pd_ver}（调用方式：{' '.join(_pd_base)}）")

        # ── auto_ingest_root 检查 ──────────────────────────
        _pd_root = _pd_settings.get("ingest", {}).get("auto_ingest_root", "")
        if not _pd_root:
            st.warning("auto_ingest_root 未配置，下载后无法归档。请在「工具配置」→「自动入库」设置主目录。")
        else:
            st.caption(f"归档根目录：{_pd_root}")

        st.markdown("---")

        # ── Session state 初始化 ───────────────────────────
        for _k, _v in [
            ("pldl_scan_result", None),
            ("pldl_dl_result", None),
            ("pldl_downloading", False),
            ("pldl_selected", {}),           # {candidate_id: bool}
            ("pldl_override_name", ""),
            ("pldl_active_session_id", ""),  # P4D-1C: 本次下载会话 ID
            ("pldl_locked_archive_dir", ""), # P4D-1C: 锁定的归档目录（字符串）
            ("pldl_locked_candidates", []),  # P4D-1C: 锁定的候选列表（用于重试查找）
        ]:
            if _k not in st.session_state:
                st.session_state[_k] = _v

        # ── 1. URL 输入 + 扫描 ────────────────────────────
        st.markdown("#### 1. 输入主页 URL")
        _pd_url = st.text_input(
            "主页 URL（B站空间 / YouTube 频道 / 抖音主页）",
            value="",
            key="pldl_url",
            placeholder="https://space.bilibili.com/xxxxx/upload/video  或  https://www.youtube.com/@ChannelName",
        )
        st.caption("B站推荐使用：`https://space.bilibili.com/<mid>/upload/video`，根主页 URL 也可用。")

        _pd_platform = "unknown"
        if _pd_url.strip():
            _pd_platform = pldl.detect_homepage_platform(_pd_url.strip())
            _platform_label = {
                "bilibili": "B站", "youtube": "YouTube",
                "douyin": "抖音 (实验)", "unknown": "未知平台",
            }.get(_pd_platform, _pd_platform)
            st.caption(f"识别平台：**{_platform_label}**")
            if _pd_platform == "douyin":
                st.info(
                    "抖音主页枚举依赖登录态，yt-dlp 无 cookies 时通常失败。"
                    "扫描失败时建议手动下载后放入 auto_ingest_root 对应子文件夹。"
                )
            if _pd_platform == "unknown":
                st.warning("无法识别平台，支持：B站空间、YouTube 频道/播放列表、抖音主页。")
            # 单视频误填提示（扫描前）
            if pldl.is_single_video_url(_pd_url.strip()):
                st.warning(
                    "当前 URL 看起来是单个视频链接，本入口用于主页/频道批量下载。"
                    "如需下载单个视频，请手动下载后放入 auto_ingest_root 对应文件夹。"
                )

        _c1, _c2 = st.columns([2, 1])
        with _c1:
            # B站走公开 API，可扫大数量；YouTube/抖音走 yt-dlp，保守限制
            if _pd_platform == "bilibili":
                _bili_key = "pldl_max_items_bili"
                if _bili_key not in st.session_state:
                    st.session_state[_bili_key] = pldl.DEFAULT_SCAN_BILI
                _inp_col, _sld_col = st.columns([1, 2])
                with _inp_col:
                    _num = st.number_input(
                        "数量（B站）", min_value=10, max_value=pldl.HARD_SCAN_BILI,
                        value=st.session_state[_bili_key], step=50,
                        key="pldl_max_num_bili",
                    )
                with _sld_col:
                    _sld = st.slider(
                        "滑动选择（B站）",
                        min_value=10, max_value=pldl.HARD_SCAN_BILI,
                        value=_num, step=50,
                        key="pldl_max_sld_bili",
                        label_visibility="collapsed",
                    )
                # 以最后改动的控件为准：若两者不一致说明 slider 被拖动
                _pd_max = _sld if _sld != _num else _num
                st.session_state[_bili_key] = _pd_max
            else:
                _other_key = "pldl_max_items_other"
                if _other_key not in st.session_state:
                    st.session_state[_other_key] = pldl.DEFAULT_SCAN_OTHER
                _inp_col, _sld_col = st.columns([1, 2])
                with _inp_col:
                    _num = st.number_input(
                        "数量", min_value=5, max_value=pldl.HARD_SCAN_OTHER,
                        value=st.session_state[_other_key], step=5,
                        key="pldl_max_num_other",
                    )
                with _sld_col:
                    _sld = st.slider(
                        "滑动选择",
                        min_value=5, max_value=pldl.HARD_SCAN_OTHER,
                        value=_num, step=5,
                        key="pldl_max_sld_other",
                        label_visibility="collapsed",
                    )
                _pd_max = _sld if _sld != _num else _num
                st.session_state[_other_key] = _pd_max
        with _c2:
            # B站扫描不依赖 yt-dlp；其他平台需要 yt-dlp
            _scan_needs_ytdlp = _pd_platform not in ("bilibili", "unknown") and _pd_base is None
            _scan_disabled = not _pd_url.strip() or _scan_needs_ytdlp
            if _scan_needs_ytdlp:
                st.caption("需要 yt-dlp 才能扫描此平台")
            if st.button("🔍 扫描", key="pldl_scan", disabled=_scan_disabled):
                st.session_state["pldl_scan_result"] = None
                st.session_state["pldl_dl_result"] = None
                st.session_state["pldl_selected"] = {}
                st.session_state["pldl_override_name"] = ""
                with st.spinner("正在枚举视频列表，请稍候…"):
                    _sr = pldl.scan_homepage(
                        _pd_url.strip(), max_items=_pd_max, settings=_pd_settings,
                    )
                st.session_state["pldl_scan_result"] = _sr
                st.rerun()

        # ── 2. 扫描结果展示 ───────────────────────────────
        _sr = st.session_state.get("pldl_scan_result")
        if _sr is not None:
            st.markdown("---")
            st.markdown("#### 2. 扫描结果")

            if not _sr.get("ok"):
                _err_info = _sr.get("error") or {}
                _err_msg = (
                    _err_info.get("message", str(_err_info))
                    if isinstance(_err_info, dict) else str(_err_info)
                )
                st.error(f"扫描失败：{_err_msg}")

                # B站专项诊断
                if _sr.get("platform") == "bilibili":
                    _bili_mid = _sr.get("bili_mid") or pldl._extract_bili_mid(_pd_url.strip())
                    if _bili_mid:
                        st.info(f"已识别 mid：`{_bili_mid}`")
                    else:
                        st.warning(
                            "无法从 URL 提取 B站 UID（mid）。\n"
                            "请确认 URL 格式，例如：`https://space.bilibili.com/1234567890123456/upload/video`"
                        )
                    if "yt-dlp 未安装" in _err_msg or "tool_missing" in str(_sr.get("error", {}).get("code", "")):
                        st.code("python -m pip install -U yt-dlp", language="bash")

                # 扫描后仍可提示单视频误填
                if pldl.is_single_video_url(_pd_url.strip()):
                    st.info(
                        "当前 URL 看起来是单个视频链接，本入口用于主页/频道批量下载，"
                        "不支持单视频扫描。请手动下载后放入 auto_ingest_root 对应文件夹。"
                    )
                elif _sr.get("experimental"):
                    st.info(
                        "抖音主页枚举不稳定，建议：\n"
                        "1. 手动在抖音 App 或浏览器下载视频\n"
                        f"2. 放入 `{_pd_root or 'auto_ingest_root'}/<账号名>__douyin/` 文件夹\n"
                        "3. 在「自动入库」tab 扫描入库"
                    )
                    st.code(_pd_url.strip(), language=None)

            else:
                _candidates = _sr.get("candidates", [])
                _author = _sr.get("author_name", "")
                _platform = _sr.get("platform", "")
                _total_seen = _sr.get("total_seen", len(_candidates))
                _truncated = _sr.get("truncated", False)

                # 扫描成功但 0 条且像单视频链接
                if not _candidates and pldl.is_single_video_url(_pd_url.strip()):
                    st.warning(
                        "扫描结果为空。当前 URL 看起来是单个视频链接，"
                        "本入口用于主页/频道批量下载，请手动下载后放入 auto_ingest_root。"
                    )

                col_a, col_b, col_c, col_d = st.columns(4)
                col_a.metric(
                    "平台",
                    {"bilibili": "B站", "youtube": "YouTube", "douyin": "抖音"}.get(_platform, _platform),
                )
                col_b.metric("账号名", _author or "(未获取)")
                col_c.metric("发现视频数", _total_seen)
                col_d.metric("本次纳入数", len(_candidates))

                if _truncated:
                    st.caption(f"已达最大扫描数量 {_pd_max}，账号可能有更多视频。增大「最大扫描数量」可纳入更多候选。")
                if _sr.get("paged_scan") and _sr.get("ok"):
                    _pages_fetched = _sr.get("pages_fetched", 1)
                    st.caption(f"已自动扫描多页（{_pages_fetched} 页），共扫描 {len(_candidates)} 条候选。")
                    for _w in _sr.get("warnings", []):
                        st.caption(f"⚠ {_w}")
                if _sr.get("experimental"):
                    st.caption("抖音实验性支持，枚举结果仅供参考。")

                # ── 候选视频选择列表 ──────────────────────
                if _candidates:
                    # 初始化选择状态（首次展示全选）
                    _sel: dict = st.session_state["pldl_selected"]
                    _need_init = not _sel or set(_sel.keys()) != {c["candidate_id"] for c in _candidates}
                    if _need_init:
                        _sel = {c["candidate_id"]: True for c in _candidates}
                        st.session_state["pldl_selected"] = _sel

                    # 计算已选数量（用于 expander 标题和 caption）
                    _n_selected = sum(
                        1 for c in _candidates
                        if st.session_state["pldl_selected"].get(c["candidate_id"], True)
                    )

                    # 全选 / 全不选按钮放在 expander 外，方便操作
                    _sel_c1, _sel_c2, _sel_info = st.columns([1, 1, 3])
                    with _sel_c1:
                        if st.button("全选", key="pldl_sel_all"):
                            st.session_state["pldl_selected"] = {c["candidate_id"]: True for c in _candidates}
                            st.rerun()
                    with _sel_c2:
                        if st.button("全不选", key="pldl_sel_none"):
                            st.session_state["pldl_selected"] = {c["candidate_id"]: False for c in _candidates}
                            st.rerun()
                    with _sel_info:
                        st.caption(f"已选 {_n_selected} / {len(_candidates)} 条")

                    # 候选列表折叠，默认收起
                    with st.expander(
                        f"选择要下载的视频（已选 {_n_selected} / 共 {len(_candidates)}）",
                        expanded=False,
                    ):
                        for _c in _candidates:
                            _cid = _c["candidate_id"]
                            _title = (_c.get("title") or "").strip()
                            _bvid = _c.get("platform_item_id") or ""
                            _date = _c.get("published_at") or ""
                            if _title:
                                _label = f"{_title}　`{_bvid}`　{_date}"
                            else:
                                _label = f"`{_bvid}`　{_date}" if _bvid else _cid
                            _checked = st.checkbox(
                                _label,
                                value=st.session_state["pldl_selected"].get(_cid, True),
                                key=f"pldl_chk_{_cid}",
                            )
                            st.session_state["pldl_selected"][_cid] = _checked

                    _selected_candidates = [
                        c for c in _candidates
                        if st.session_state["pldl_selected"].get(c["candidate_id"], True)
                    ]
                else:
                    _selected_candidates = []

                # ── 3. 归档名 + 确认下载 ─────────────────
                st.markdown("---")
                st.markdown("#### 3. 确认下载")

                # 账号名为空时强制提示手动填写（B站 API 无法自动获取时的标准流程）
                _need_override = not _author or _author.lower() == "unknown"
                if _need_override:
                    st.warning(
                        "未能自动获取账号名，请手动填写归档账号名。\n\n"
                        "账号名用于归档目录命名：`auto_ingest_root/<账号名>__bilibili/`"
                    )

                _override_name = st.text_input(
                    "归档账号名" + ("（必填）" if _need_override else "（留空则使用扫描获取的账号名）"),
                    value=st.session_state.get("pldl_override_name", ""),
                    key="pldl_override_name_input",
                    placeholder="例：张三  或  灰狼",
                )
                st.session_state["pldl_override_name"] = _override_name.strip()

                _archive_dir, _archive_err = pldl.resolve_archive_dir(
                    _author, _platform, _pd_root,
                    override_name=st.session_state["pldl_override_name"],
                )
                if _archive_err:
                    st.error(f"归档目录异常：{_archive_err}")
                else:
                    st.caption(f"下载后归档至：`{_archive_dir}`")

                _dl_result = st.session_state.get("pldl_dl_result")
                _is_downloading = st.session_state.get("pldl_downloading", False)

                # ── P4D-1C/1D: 加载最近下载报告（刷新恢复）────────
                if not _dl_result and not _is_downloading and _archive_dir:
                    _existing_report = pldl.load_report(_archive_dir)
                    if _existing_report and _existing_report.get("finished_at"):
                        if st.button("📂 加载最近下载报告", key="pldl_load_report"):
                            _rpt = _existing_report
                            _recovered_failures = [
                                {
                                    "candidate_id": it.get("candidate_id", ""),
                                    "title": it.get("title", ""),
                                    "url": it.get("source_url") or it.get("url", ""),
                                    "error": it.get("error", ""),
                                    "status": it.get("status", "failed"),
                                    "retry_count": it.get("retry_count", 0),
                                }
                                for it in _rpt.get("items", [])
                                if it.get("status") == "failed"
                            ]
                            # P4D-1D: 从 report items 恢复 locked_candidates
                            _recovered_cands = []
                            for _it in _rpt.get("items", []):
                                _rc = pldl.candidate_from_report_item(_it)
                                if _rc:
                                    _recovered_cands.append(_rc)
                            st.session_state["pldl_locked_candidates"] = _recovered_cands
                            st.session_state["pldl_dl_result"] = {
                                "ok": True,
                                "total": _rpt.get("total", 0),
                                "success": _rpt.get("success", 0),
                                "failed": _rpt.get("failed", 0),
                                "failures": _recovered_failures,
                                "archive_dir": _rpt.get("archive_dir", str(_archive_dir)),
                                "finished_at": _rpt.get("finished_at", ""),
                                "_recovered": True,
                            }
                            st.session_state["pldl_active_session_id"] = _rpt.get("session_id", "")
                            st.session_state["pldl_locked_archive_dir"] = _rpt.get("archive_dir", str(_archive_dir))
                            st.rerun()
                        st.caption(
                            f"发现已有报告（{_existing_report.get('session_id', '')}，"
                            f"完成于 {_existing_report.get('finished_at', '')[:19]}）"
                        )

                if not _dl_result and not _is_downloading:
                    _dl_needs_ytdlp = _pd_base is None
                    _confirm_disabled = (
                        bool(_archive_err)
                        or not _selected_candidates
                        or (_need_override and not st.session_state["pldl_override_name"])
                        or _dl_needs_ytdlp
                    )
                    if not _selected_candidates:
                        st.caption("请至少选中一条视频才能下载。")
                    if _need_override and not st.session_state["pldl_override_name"]:
                        st.caption("请填写归档账号名后才能下载。")
                    if _dl_needs_ytdlp:
                        st.error(
                            "下载需要 yt-dlp（所有平台），当前未检测到可用的 yt-dlp。"
                            "请在「工具配置」填写 yt_dlp_exe，或执行 pip install yt-dlp 后刷新页面。"
                        )

                    st.caption(
                        f"已扫描 {len(_candidates)} 条　已选择下载 {len(_selected_candidates)} 条"
                    )

                    if st.button(
                        f"⬇ 确认下载（{len(_selected_candidates)} 条）",
                        key="pldl_confirm_dl",
                        disabled=_confirm_disabled,
                    ):
                        # P4D-1C: 生成会话 ID，锁定 archive_dir 和候选列表
                        _session_id = pldl.generate_session_id(_platform, _author)
                        st.session_state["pldl_active_session_id"] = _session_id
                        st.session_state["pldl_locked_archive_dir"] = str(_archive_dir)
                        st.session_state["pldl_locked_candidates"] = list(_selected_candidates)
                        _locked_dir = _archive_dir  # 本次下载全程用此变量，不受后续 UI 输入影响

                        st.session_state["pldl_downloading"] = True
                        st.session_state["pldl_dl_result"] = None

                        # 构建初始报告并落盘
                        _init_report = pldl._build_initial_report(
                            session_id=_session_id,
                            platform=_platform,
                            author_name=_author,
                            source_url=_pd_url.strip(),
                            archive_dir=_locked_dir,
                            candidates=_selected_candidates,
                        )
                        _rpt_err = pldl.write_report(_init_report, _locked_dir)
                        if _rpt_err:
                            st.warning(f"报告初始化失败（不影响下载）：{_rpt_err}")

                        _prog_bar = st.progress(0, text="准备下载…")
                        _status_txt = st.empty()
                        _done_count = [0]
                        _success_count = [0]
                        _fail_count = [0]

                        def _progress_cb(done: int, total: int, last: dict) -> None:
                            _done_count[0] = done
                            if last.get("ok"):
                                _success_count[0] += 1
                            else:
                                _fail_count[0] += 1
                            pct = int(done / total * 100) if total else 100
                            _prog_bar.progress(
                                pct,
                                text=f"下载中 {done}/{total}　✓{_success_count[0]}　✗{_fail_count[0]}",
                            )
                            _status_txt.caption(f"当前：{last.get('title', '')[:40]}")

                        _result = pldl.batch_download(
                            _selected_candidates,
                            _locked_dir,
                            settings=_pd_settings,
                            progress_cb=_progress_cb,
                            report=_init_report,
                        )
                        _prog_bar.progress(100, text="下载完成")
                        _status_txt.empty()
                        st.session_state["pldl_dl_result"] = _result
                        st.session_state["pldl_downloading"] = False
                        st.rerun()

                # ── 4. 下载结果 + 失败列表 ────────────────
                if _dl_result:
                    st.markdown("---")
                    st.markdown("#### 4. 下载结果")

                    if _dl_result.get("_recovered"):
                        st.info("已从本地报告恢复（仅展示结果，无法恢复下载进程）。")

                    _rc1, _rc2, _rc3, _rc4 = st.columns(4)
                    _rc1.metric("总数", _dl_result.get("total", 0))
                    _rc2.metric("成功", _dl_result.get("success", 0))
                    _rc3.metric("失败", _dl_result.get("failed", 0))
                    _rc4.metric("归档目录", str(_dl_result.get("archive_dir", ""))[-32:])

                    # P4D-1C: 用锁定的 archive_dir（重试永远用 session 锁定值）
                    _locked_dir_str = st.session_state.get("pldl_locked_archive_dir", "")
                    _retry_archive_dir = Path(_locked_dir_str) if _locked_dir_str else _archive_dir

                    # P4D-1D: 三级查找辅助 —— locked_candidates → report item → 明确报错
                    _locked_cands = st.session_state.get("pldl_locked_candidates") or []

                    def _find_retry_candidate(candidate_id: str) -> tuple[dict | None, str]:
                        """
                        返回 (candidate_dict, error_msg)。
                        error_msg 非空时表示无法恢复，应在 UI 展示。
                        """
                        # 1) session 锁定列表
                        c = next((x for x in _locked_cands if x.get("candidate_id") == candidate_id), None)
                        if c:
                            return c, ""
                        # 2) 从 report JSON 重建
                        _rpt = pldl.load_report(_retry_archive_dir)
                        if _rpt:
                            _it = next((x for x in _rpt.get("items", []) if x.get("candidate_id") == candidate_id), None)
                            if _it:
                                c = pldl.candidate_from_report_item(_it)
                                if c:
                                    return c, ""
                        # 3) 无法恢复
                        return None, "无法恢复该视频信息，请复制链接手动处理"

                    _failures: list[dict] = _dl_result.get("failures", [])
                    # P4D-1C: active failures 只筛选 status == "failed"（不再用 skipped=True）
                    _active_failures = [f for f in _failures if f.get("status") == "failed"]

                    if not _active_failures:
                        st.success("全部完成！请前往「自动入库」tab 扫描入库。")
                    else:
                        st.warning(f"有 {len(_active_failures)} 条待处理（失败或待重试），可逐条重试或手动处理。")

                        # 重试全部按钮
                        _retriable = [
                            f for f in _active_failures
                            if f.get("retry_count", 0) < 3
                            and f.get("error_code") != "not_accessible"
                        ]
                        if _retriable:
                            if st.button(f"🔄 重试全部失败（{len(_retriable)} 条）", key="pldl_retry_all"):
                                _retry_all_errors: list[str] = []
                                for _rf in _retriable:
                                    _rc_obj, _rc_err = _find_retry_candidate(_rf.get("candidate_id", ""))
                                    if not _rc_obj:
                                        _retry_all_errors.append(
                                            f"{_rf.get('title', '(无标题)')[:30]}：{_rc_err}"
                                        )
                                        continue
                                    _rr = pldl.download_single_video(
                                        _rc_obj, _retry_archive_dir, _pd_settings,
                                    )
                                    if _rr.get("ok"):
                                        _dl_result["success"] = _dl_result.get("success", 0) + 1
                                        _dl_result["failed"] = max(0, _dl_result.get("failed", 1) - 1)
                                        _rf["status"] = "success"
                                        _rf["error"] = ""
                                        _rf["error_code"] = ""
                                    else:
                                        _rf["retry_count"] = _rf.get("retry_count", 0) + 1
                                        _rf["error"] = _rr.get("error", "重试失败")
                                        _rf["error_code"] = _rr.get("error_code", "")

                                for _re_msg in _retry_all_errors:
                                    st.warning(_re_msg)

                                # 更新报告落盘
                                _locked_report = pldl.load_report(_retry_archive_dir)
                                if _locked_report:
                                    _item_map = {it["candidate_id"]: it for it in _locked_report.get("items", [])}
                                    for _rf in _failures:
                                        if _rf["candidate_id"] in _item_map:
                                            _item_map[_rf["candidate_id"]]["status"] = _rf["status"]
                                            _item_map[_rf["candidate_id"]]["error"] = _rf.get("error", "")
                                            _item_map[_rf["candidate_id"]]["error_code"] = _rf.get("error_code", "")
                                            _item_map[_rf["candidate_id"]]["retry_count"] = _rf.get("retry_count", 0)
                                            _item_map[_rf["candidate_id"]]["updated_at"] = pldl._now_iso()
                                    _locked_report["success"] = _dl_result.get("success", 0)
                                    _locked_report["failed"] = len([f for f in _failures if f.get("status") == "failed"])
                                    _rpt_err = pldl.write_report(_locked_report, _retry_archive_dir)
                                    if _rpt_err:
                                        st.warning(f"报告更新失败：{_rpt_err}")

                                st.session_state["pldl_dl_result"] = _dl_result
                                st.rerun()

                        # 逐条展示
                        for _fi, _f in enumerate(_active_failures):
                            _rc_count = _f.get("retry_count", 0)
                            _max_retried = _rc_count >= 3
                            _is_restricted = _f.get("error_code") == "not_accessible"
                            _expander_label = (
                                ("⛔ 付费/限制：" if _is_restricted else "")
                                + (f"[已重试 {_rc_count} 次] " if _rc_count else "")
                                + f"失败：{_f.get('title', '(无标题)')[:50]}"
                            )

                            with st.expander(_expander_label, expanded=True):
                                _fa, _fb = st.columns([3, 1])
                                with _fa:
                                    if _is_restricted:
                                        st.warning(
                                            "付费/充电专属/大会员/区域限制视频，无法直接下载，已跳过。"
                                            "如有授权可手动下载后放入归档目录。"
                                        )
                                    else:
                                        st.caption(f"失败原因：{_f.get('error', '')[:200]}")
                                    _fu = _f.get("url", "")
                                    if _fu:
                                        st.code(_fu, language=None)
                                        st.caption("复制上方链接可手动下载后放入归档目录")
                                with _fb:
                                    _retry_disabled = _max_retried or _is_restricted
                                    if st.button(
                                        "重试" if not _max_retried else "已达上限",
                                        key=f"pldl_retry_{_fi}_{_f.get('candidate_id', '')}",
                                        disabled=_retry_disabled,
                                        help="付费/限制视频无法重试，请手动处理" if _is_restricted else None,
                                    ):
                                        _retry_cand, _retry_err = _find_retry_candidate(_f.get("candidate_id", ""))
                                        if not _retry_cand:
                                            st.error(_retry_err)
                                        elif _retry_archive_dir:
                                            with st.spinner("重试中…"):
                                                _rr = pldl.download_single_video(
                                                    _retry_cand, _retry_archive_dir, _pd_settings,
                                                )
                                            if _rr.get("ok"):
                                                _dl_result["success"] = _dl_result.get("success", 0) + 1
                                                _dl_result["failed"] = max(0, _dl_result.get("failed", 1) - 1)
                                                _f["status"] = "success"
                                                _f["error"] = ""
                                                _f["error_code"] = ""
                                            else:
                                                _f["retry_count"] = _rc_count + 1
                                                _f["error"] = _rr.get("error", "重试失败")
                                                _f["error_code"] = _rr.get("error_code", "")

                                            # 更新报告落盘
                                            _locked_report = pldl.load_report(_retry_archive_dir)
                                            if _locked_report:
                                                _item_map = {it["candidate_id"]: it for it in _locked_report.get("items", [])}
                                                if _f["candidate_id"] in _item_map:
                                                    _item_map[_f["candidate_id"]]["status"] = _f["status"]
                                                    _item_map[_f["candidate_id"]]["error"] = _f.get("error", "")
                                                    _item_map[_f["candidate_id"]]["error_code"] = _f.get("error_code", "")
                                                    _item_map[_f["candidate_id"]]["retry_count"] = _f.get("retry_count", 0)
                                                    _item_map[_f["candidate_id"]]["updated_at"] = pldl._now_iso()
                                                _locked_report["success"] = _dl_result.get("success", 0)
                                                _locked_report["failed"] = len([f for f in _failures if f.get("status") == "failed"])
                                                _rpt_err = pldl.write_report(_locked_report, _retry_archive_dir)
                                                if _rpt_err:
                                                    st.warning(f"报告更新失败：{_rpt_err}")

                                            st.session_state["pldl_dl_result"] = _dl_result
                                            st.rerun()

                                    if st.button(
                                        "跳过",
                                        key=f"pldl_skip_{_fi}_{_f.get('candidate_id', '')}",
                                    ):
                                        _f["status"] = "skipped"  # P4D-1C: 跳过用 skipped
                                        _dl_result["failed"] = max(0, _dl_result.get("failed", 1) - 1)
                                        _dl_result["skipped"] = _dl_result.get("skipped", 0) + 1

                                        # 更新报告落盘
                                        _locked_report = pldl.load_report(_retry_archive_dir)
                                        if _locked_report:
                                            _item_map = {it["candidate_id"]: it for it in _locked_report.get("items", [])}
                                            if _f["candidate_id"] in _item_map:
                                                _item_map[_f["candidate_id"]]["status"] = "skipped"
                                                _item_map[_f["candidate_id"]]["updated_at"] = pldl._now_iso()
                                            _locked_report["failed"] = len([f for f in _failures if f.get("status") == "failed"])
                                            _locked_report["skipped"] = _locked_report.get("skipped", 0) + 1
                                            _rpt_err = pldl.write_report(_locked_report, _retry_archive_dir)
                                            if _rpt_err:
                                                st.warning(f"报告更新失败：{_rpt_err}")

                                        st.session_state["pldl_dl_result"] = _dl_result
                                        st.rerun()

                    st.caption(
                        "下载归档完成后，前往「自动入库」tab，点击「扫描主目录」即可看到新文件夹。"
                        "后续流程：导师身份确认 → 转写 → 卡片化 → 卡片审核 → 审核合并。"
                    )

                    if st.button("清空本次结果，重新开始", key="pldl_reset"):
                        st.session_state["pldl_scan_result"] = None
                        st.session_state["pldl_dl_result"] = None
                        st.session_state["pldl_downloading"] = False
                        st.session_state["pldl_selected"] = {}
                        st.session_state["pldl_override_name"] = ""
                        st.session_state["pldl_active_session_id"] = ""
                        st.session_state["pldl_locked_archive_dir"] = ""
                        st.session_state["pldl_locked_candidates"] = []
                        st.rerun()
