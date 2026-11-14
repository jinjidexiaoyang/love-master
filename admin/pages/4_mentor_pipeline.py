"""
admin/pages/4_mentor_pipeline.py — 导师采集流水线控制台(P4C-Pipeline)

把转写/切块/卡片化/合并/画像刷新这套流水线封装成「按阶段卡片」,
每张卡片显示进度、统计、最近日志和按钮,所有重活在已有任务队列后台子进程里跑。

UI 不直接执行付费 API 或写正式库;真实合并/画像刷新需要二次确认。

主流程对照(都走 services.mentor_task_service + scripts/run_mentor_ingest_task.py):
  1. 转写 dry-run            → task_type=transcribe_dry_run
  2. 开始/续跑转写            → task_type=transcribe
  3. 重试失败转写            → task_type=transcribe_retry_failed
  4. 切 chunks(不调 LLM)    → task_type=build_chunks
  5. 样本卡片化 20 条         → task_type=build_cards_sample
  6. 全量卡片化 / 续跑        → task_type=build_cards_full
  7. approve dry-run         → task_type=approve_dry_run
  8. 真实 approve 合并        → task_type=approve_merge      (双重确认)
  9. 刷新 mentor_profiles     → task_type=generate_profiles  (二次确认 + 付费 API 提示)
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import streamlit as st

# admin/pages/<page>.py → 项目根 = parents[2]
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from services.mentor_ingest_service import (  # noqa: E402
    STAGING_ROOT, batch_dir, load_manifest, load_source_items,
)
from services import mentor_task_service as tasksvc  # noqa: E402
from services import mentor_identity_service as ident  # noqa: E402
from services import mentor_pipeline_progress as pp  # noqa: E402
from utils.ui_helpers import apply_theme, theme_toggle_button  # noqa: E402
from admin._auth import require_admin  # noqa: E402

st.set_page_config(page_title="流水线控制台 · 恋爱大师管理后台",
                   page_icon="🛠", layout="wide")
theme_toggle_button()
apply_theme()
require_admin()

# ─── 侧边栏 ───────────────────────────────────────────────
with st.sidebar:
    st.markdown('<div style="padding:8px 0 16px 0;font-size:1.3rem;font-weight:700;">🛠 恋爱大师管理后台</div>',
                unsafe_allow_html=True)
    st.page_link("app.py", label="🏠 Admin 首页")
    st.page_link("pages/1_mentor_ingest.py", label="🎬 导师资源后台")
    st.page_link("pages/2_knowledge_base.py", label="📚 知识库")
    st.page_link("pages/3_style_core.py", label="🧬 风格核心")
    st.page_link("pages/4_mentor_pipeline.py", label="🛠 流水线控制台")

st.markdown('<div class="page-title">导师采集流水线控制台</div>', unsafe_allow_html=True)
st.markdown('<div class="page-subtitle">'
            '统一调度:转写 / 切块 / 卡片化 / 合并 / 画像刷新 · 后台子进程串行 · 不阻塞 UI'
            '</div>', unsafe_allow_html=True)

_STATUS_ICON = {
    "pending": "⏳", "running": "🔄", "success": "✅",
    "failed": "❌", "skipped": "⏭", "cancelled": "🚫",
}


# ─── batch 选择 ───────────────────────────────────────────
def _list_batches_sorted() -> list[str]:
    """从 staging 目录扫描 batch,按 LastWriteTime 倒序。"""
    if not STAGING_ROOT.exists():
        return []
    entries = []
    for d in STAGING_ROOT.iterdir():
        if d.is_dir() and (d / "manifest.json").exists():
            try:
                mt = d.stat().st_mtime
            except Exception:
                mt = 0
            entries.append((mt, d.name))
    entries.sort(reverse=True)
    return [name for _, name in entries]


_batch_ids = _list_batches_sorted()
if not _batch_ids:
    st.info("暂无 batch。请先在「导师资源后台」自动入库或新建采集后再来这里。")
    st.stop()

_default_idx = 0
_qs_batch = st.query_params.get("batch") if hasattr(st, "query_params") else None
if _qs_batch and _qs_batch in _batch_ids:
    _default_idx = _batch_ids.index(_qs_batch)

bc1, bc2 = st.columns([4, 1])
with bc1:
    batch_id = st.selectbox("选择 batch_id(按修改时间倒序)", _batch_ids,
                            index=_default_idx, key="pl_batch")
with bc2:
    if st.button("🔄 刷新页面", key="pl_refresh"):
        st.rerun()

manifest = load_manifest(batch_id) or {}
bd = batch_dir(batch_id)


# ─── batch 摘要 ───────────────────────────────────────────
def _count_json_list(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        v = json.loads(path.read_text(encoding="utf-8"))
        return len(v) if isinstance(v, list) else 0
    except Exception:
        return 0


def _identity_check(manifest: dict) -> dict:
    """检查 identity_map 命中情况。
    返回 {mapped: bool, public_mentor_id, public_name, real_mentor_name, message}。"""
    real = (manifest.get("real_mentor_name") or "").strip()
    course = (manifest.get("target_course") or "").strip()
    pmid = (manifest.get("public_mentor_id") or "").strip()
    pname = (manifest.get("public_name") or "").strip()
    out = {
        "mapped": False, "public_mentor_id": pmid, "public_name": pname,
        "real_mentor_name": real, "course": course, "message": "",
    }
    try:
        ids = ident.list_identities()
    except Exception as e:
        out["message"] = f"读取 identity 失败: {e}"
        return out
    if pmid:
        for it in ids:
            if it.get("public_mentor_id") == pmid:
                out["mapped"] = True
                out["public_name"] = it.get("public_name") or pname
                out["real_mentor_name"] = it.get("real_mentor_name") or real
                return out
    if real:
        for it in ids:
            if it.get("real_mentor_name") == real:
                out["mapped"] = True
                out["public_mentor_id"] = it.get("public_mentor_id") or ""
                out["public_name"] = it.get("public_name") or pname
                return out
    if course:
        for it in ids:
            if course in (it.get("aggregate_courses") or []):
                out["mapped"] = True
                out["public_mentor_id"] = it.get("public_mentor_id") or ""
                out["public_name"] = it.get("public_name") or pname
                return out
    out["message"] = ("未在 mentor_identity_map 中找到匹配项,"
                      "合并前请先到「导师资源后台 → 导师身份」配置脱敏映射。")
    return out


_items = load_source_items(batch_id)
_n_items = len(_items)
_n_with_transcript = sum(1 for it in _items if it.get("transcript_path"))

_chunks_path = bd / "chunks.json"
_drafts_path = bd / "draft_cards.json"
_rejected_path = bd / "rejected_cards.json"
_approved_path = bd / "approved_cards.json"
_dry_snapshot_path = bd / "approve_dry_run_last.json"
_approval_path = bd / "approval.json"

_n_chunks = _count_json_list(_chunks_path)
_n_drafts = _count_json_list(_drafts_path)
_n_rejected = _count_json_list(_rejected_path)
_n_approved = _count_json_list(_approved_path)

_approval_decision = ""
if _approval_path.exists():
    try:
        _ap = json.loads(_approval_path.read_text(encoding="utf-8"))
        _approval_decision = _ap.get("decision") or _ap.get("status") or ""
    except Exception:
        _approval_decision = ""

_id_check = _identity_check(manifest)

st.markdown("### Batch 概览")
m1, m2, m3, m4, m5, m6 = st.columns(6)
m1.metric("source_items", _n_items)
m2.metric("已转写", _n_with_transcript)
m3.metric("chunks", _n_chunks)
m4.metric("draft_cards", _n_drafts)
m5.metric("rejected", _n_rejected)
m6.metric("approved_cards", _n_approved if _approved_path.exists() else 0)

mc1, mc2 = st.columns(2)
with mc1:
    st.caption(f"manifest.real_mentor_name: `{manifest.get('real_mentor_name','-')}` · "
               f"public_name: `{manifest.get('public_name','-')}` · "
               f"target_course: `{manifest.get('target_course','-')}`")
    st.caption(f"manifest.status: `{manifest.get('status','-')}`")
    st.caption(f"approval.json: `{_approval_decision or '(未审核)'}`")
with mc2:
    if _id_check["mapped"]:
        st.success(
            f"✅ identity 已映射:`{_id_check['public_mentor_id']}` · "
            f"public_name=`{_id_check['public_name']}` · "
            f"real=`{_id_check['real_mentor_name']}`"
        )
    else:
        st.error(
            f"⛔ identity_map 未命中:`real={_id_check['real_mentor_name'] or '-'}` / "
            f"`course={_id_check['course'] or '-'}` · {_id_check['message']}"
        )

st.markdown("---")

# ─── 一些公共状态 ────────────────────────────────────────
_active_task = tasksvc.has_active_task(batch_id)
_running_tasks = tasksvc.list_tasks(batch_id, status="running")
if _running_tasks:
    rt = _running_tasks[0]
    st.info(
        f"🔄 当前活跃任务:`{rt.get('task_type','')}` · {rt.get('task_id','')} · "
        f"progress={rt.get('progress',0)}　v1 串行,等它结束才能起新任务。"
        " 点右上「🔄 刷新页面」查看最新进度。"
    )

# 从 mentor_identity_map 拿 identity_no_redact 风险确认 session 状态
_force_unsafe_key = f"pl_id_no_redact_{batch_id}"
_force_unsafe = st.session_state.get(_force_unsafe_key, False)
if not _id_check["mapped"]:
    _force_unsafe = st.checkbox(
        "我已确认此 batch **不需要** 导师身份脱敏(允许不命中 identity_map 进行 approve)",
        value=_force_unsafe, key=_force_unsafe_key,
    )

_can_unsafe_approve = _id_check["mapped"] or _force_unsafe


# ─── 阶段进度卡渲染 ──────────────────────────────────────
def _latest_task(task_types: list[str]) -> dict | None:
    """返回该 batch 内,task_type 在 task_types 集合中最新一个任务(按 created_at)。"""
    candidates: list[dict] = []
    for tt in task_types:
        candidates.extend(tasksvc.list_tasks(batch_id, task_type=tt))
    if not candidates:
        return None
    candidates.sort(key=lambda t: t.get("created_at", ""), reverse=True)
    return candidates[0]


def _stage_card(title: str, task_types: list[str], *,
                expander: bool = True, body=None, default_open: bool = False):
    """阶段卡片容器。body 是个 callable,接收 latest_task,渲染状态摘要 + 按钮。"""
    latest = _latest_task(task_types)
    status = (latest or {}).get("status", "—")
    icon = _STATUS_ICON.get(status, "🔘")
    extra = (latest or {}).get("extra") or {}
    prog = extra.get("parsed_progress") or {}
    cur = prog.get("current") or 0
    tot = prog.get("total") or 0
    header = f"{icon} {title} · {status}"
    if tot:
        header += f" · {cur}/{tot}"
    elif latest and latest.get("progress"):
        header += f" · {latest.get('progress')}%"

    container = st.expander(header, expanded=default_open) if expander else st.container()
    with container:
        if latest:
            mc = st.columns(4)
            mc[0].caption(f"task_id: `{(latest or {}).get('task_id','-')}`")
            mc[1].caption(f"started: {(latest or {}).get('started_at','-') or '-'}")
            mc[2].caption(f"finished: {(latest or {}).get('finished_at','-') or '-'}")
            mc[3].caption(f"retry: {(latest or {}).get('retry_count',0)}/{(latest or {}).get('max_retries',0)}")

            if tot:
                pct = pp.progress_percent(prog)
                st.progress(min(100, max(0, pct)) / 100.0,
                            text=f"{cur}/{tot} · {pct}%")
            stat_cols = []
            for k, label in (("ok", "ok"), ("fail", "fail"), ("skip", "skip"),
                             ("accepted", "accepted"), ("rejected", "rejected"),
                             ("chunks_count", "chunks"),
                             ("merged_cards", "merged_cards"),
                             ("merged_chunks", "merged_chunks"),
                             ("total_cards", "total_cards"),
                             ("view_count", "views")):
                if k in prog:
                    stat_cols.append((label, prog[k]))
            if stat_cols:
                cs = st.columns(len(stat_cols))
                for i, (lbl, val) in enumerate(stat_cols):
                    cs[i].metric(lbl, val)
            if prog.get("current_id"):
                st.caption(f"当前 item: `{prog['current_id']}`")
            if prog.get("current_view"):
                st.caption(f"当前 view: `{prog['current_view']}`"
                           f"(cards={prog.get('current_view_cards','?')})")
            if prog.get("backend"):
                st.caption(f"backend: `{prog['backend']}`")
            if prog.get("last_line"):
                st.caption(f"last: {prog['last_line']}")
            if prog.get("error_summary"):
                st.warning(prog["error_summary"])
            if (latest or {}).get("error"):
                st.error((latest or {}).get("error", "")[:1500])

        if body is not None:
            body(latest)


def _launch(task_type: str, *, extra: dict | None = None,
            success_msg: str = "", confirm_required: bool = False) -> None:
    """通用 create_and_launch 包装,带活跃任务保护。"""
    if _active_task:
        st.warning("已有任务运行中(v1 串行),请等待完成。")
        return
    res = tasksvc.create_and_launch(batch_id, task_type, extra=extra or {})
    if res.get("ok"):
        st.success(success_msg or f"已启动 {task_type} 任务 pid={res.get('pid')}")
    else:
        st.error(res.get("error") or "启动失败")
    st.rerun()


# ─── ① 转写 ───────────────────────────────────────────────
st.markdown("### ① 转写")


def _body_transcribe(latest: dict | None):
    bcol = st.columns(3)
    with bcol[0]:
        if st.button("🧪 dry-run", key="pl_tr_dry", disabled=_active_task,
                     help="只列出待转写项,不真正调用 ASR"):
            _launch("transcribe_dry_run",
                    extra={"backend": "whisper_cpp", "dry_run": True},
                    success_msg="已启动转写 dry-run 任务")
    with bcol[1]:
        if st.button("▶ 开始 / 续跑转写", key="pl_tr_run",
                     disabled=_active_task,
                     help="跑 whisper_cpp,已转写的会跳过"):
            _launch("transcribe", extra={"backend": "whisper_cpp"},
                    success_msg="已启动转写任务")
    with bcol[2]:
        if st.button("↻ 重试失败转写", key="pl_tr_retry",
                     disabled=_active_task,
                     help="只对状态 failed 的 item 重跑"):
            _launch("transcribe_retry_failed",
                    extra={"backend": "whisper_cpp", "retry_failed": True},
                    success_msg="已启动重试失败转写任务")


_stage_card("转写(transcribe_videos.py)",
            ["transcribe", "transcribe_dry_run", "transcribe_retry_failed"],
            body=_body_transcribe,
            default_open=any(t.get("status") == "running" for t in
                             (tasksvc.list_tasks(batch_id, task_type="transcribe") +
                              tasksvc.list_tasks(batch_id, task_type="transcribe_dry_run") +
                              tasksvc.list_tasks(batch_id, task_type="transcribe_retry_failed"))))


# ─── ② 切 chunks ─────────────────────────────────────────
st.markdown("### ② 切 chunks")


def _body_chunks(latest: dict | None):
    if _chunks_path.exists():
        st.success(f"chunks.json 已存在:{_n_chunks} 条 · {_chunks_path}")
    if st.button("✂ 仅清洗 + 切 chunks(--skip-cards)", key="pl_chunks_run",
                 disabled=_active_task,
                 help="只切块、不调用 DeepSeek,纯本地 + 免费"):
        _launch("build_chunks", extra={"skip_cards": True},
                success_msg="已启动切块任务")


_stage_card("切块(build_ingest_cards.py --skip-cards)",
            ["build_chunks"], body=_body_chunks)


# ─── ③ 卡片化 ────────────────────────────────────────────
st.markdown("### ③ 卡片化(会调用 DeepSeek 付费 API)")


def _body_cards(latest: dict | None):
    if _drafts_path.exists():
        st.caption(f"draft_cards.json:{_n_drafts} 张 · rejected_cards.json:{_n_rejected} 张")
    cols = st.columns(2)
    with cols[0]:
        if st.button("🧪 样本卡片化 20 条(--sample 20 --resume)",
                     key="pl_cards_sample", disabled=_active_task,
                     help="只跑前 20 个 chunk,用来人工对一下卡片质量"):
            _launch("build_cards_sample",
                    extra={"sample": 20, "resume": True},
                    success_msg="已启动样本卡片化任务")
    with cols[1]:
        if st.button("▶ 全量 / 续跑卡片化(--resume)",
                     key="pl_cards_full", disabled=_active_task,
                     help="续跑全量。已处理过的 chunk_id 会跳过"):
            _launch("build_cards_full", extra={"resume": True},
                    success_msg="已启动全量卡片化任务")
    st.caption("⚠ 卡片化按 chunk 调 DeepSeek,会产生费用。脚本自带断点续跑。")


_stage_card("卡片化(build_ingest_cards.py)",
            ["build_cards", "build_cards_sample", "build_cards_full"],
            body=_body_cards)


# ─── ④ approve dry-run ───────────────────────────────────
st.markdown("### ④ approve dry-run")


def _body_dry(latest: dict | None):
    reviewed = _approved_path.exists()
    if reviewed:
        st.success(f"将走 reviewed 模式(approved_cards.json,{_n_approved} 张)")
    else:
        st.warning("approved_cards.json 不存在 → reviewed 模式不可用。"
                   "如需仍跑 dry-run,请勾选下方 allow_unreviewed(只是预览,不写正式库)。")

    allow_chk = st.checkbox(
        "允许跳过逐条审核(--allow-unreviewed)",
        key=f"pl_dry_allow_{batch_id}",
        disabled=reviewed,
        help="reviewed 模式可用时,默认不需要勾这个;只在没有 approved_cards.json 时使用",
    )

    if st.button("🧪 运行 approve dry-run", key="pl_dry_run",
                 disabled=_active_task):
        _launch("approve_dry_run",
                extra={"allow_unreviewed": bool(allow_chk and not reviewed)},
                success_msg="已启动 approve dry-run 任务")

    if _dry_snapshot_path.exists():
        st.markdown("**最近一次 dry-run 快照(approve_dry_run_last.json)**")
        try:
            snap = json.loads(_dry_snapshot_path.read_text(encoding="utf-8"))
        except Exception as e:
            snap = {"_error": str(e)}
        if isinstance(snap, dict):
            st.caption(f"saved_at: {snap.get('saved_at','-')} · "
                       f"batch: {snap.get('batch_id','-')}")
            st.json(snap.get("result") or snap)


_stage_card("approve dry-run(approve_mentor_ingest_batch.py --dry-run)",
            ["approve_dry_run"], body=_body_dry)


# ─── ⑤ 真实 approve ──────────────────────────────────────
st.markdown("### ⑤ 真实 approve(写入正式库)")


def _has_recent_dry_run_for_batch() -> tuple[bool, str]:
    """查最近一个 approve_dry_run 任务是否对当前 batch 成功。"""
    drys = tasksvc.list_tasks(batch_id, task_type="approve_dry_run", status="success")
    if not drys:
        return False, "尚未跑过 approve dry-run。"
    latest = drys[0]  # list_tasks 已按 created_at 倒序
    extra = latest.get("extra") or {}
    prog = extra.get("parsed_progress") or {}
    if prog.get("dry_run_result") and prog.get("dry_run_result", {}).get("ok") is True:
        return True, latest.get("task_id", "")
    if _dry_snapshot_path.exists():
        return True, latest.get("task_id", "")
    return False, "最近一次 dry-run 未成功,请先重跑。"


def _body_merge(latest: dict | None):
    reviewed = _approved_path.exists()
    dry_ok, dry_info = _has_recent_dry_run_for_batch()

    st.error(
        "⚠ 真实 approve 会把 draft_cards / approved_cards 永久写入正式 "
        "data/mentor_knowledge/ 。必须满足:① dry-run 已成功;② batch_id 一致;"
        "③ identity_map 已命中(或显式确认无需脱敏);④ 输入确认文本;"
        "⑤ 如使用 --allow-unreviewed,需额外输入 ALLOW_UNREVIEWED。"
    )

    cols = st.columns(2)
    with cols[0]:
        st.caption(f"dry-run 状态: "
                   f"{'✅ 已成功' if dry_ok else '⛔ ' + dry_info}")
        st.caption(f"reviewed 模式: {'✅ 可用' if reviewed else '⛔ approved_cards.json 不存在'}")
    with cols[1]:
        st.caption(f"identity 映射: "
                   f"{'✅ 命中' if _id_check['mapped'] else ('✅ 已确认无需脱敏' if _force_unsafe else '⛔ 未命中')}")
        st.caption(f"已审核 approval.json: `{_approval_decision or '(未审核)'}`")

    confirm_chk = st.checkbox("我已运行 dry-run 并确认要真实合并",
                              key=f"pl_merge_chk_{batch_id}")
    confirm_text = st.text_input(
        '请输入 "确认合并" 解锁(防误触)',
        key=f"pl_merge_text_{batch_id}", placeholder="确认合并",
    )
    typed_bid = st.text_input(
        "再次输入完整 batch_id 解锁(防选错 batch)",
        key=f"pl_merge_bid_{batch_id}", placeholder=batch_id,
    )

    use_unreviewed = False
    unreviewed_typed = ""
    if not reviewed:
        use_unreviewed = st.checkbox(
            "使用 --allow-unreviewed(高级 · 跳过逐条审核)",
            key=f"pl_merge_au_{batch_id}",
            help="仅在 approved_cards.json 不存在且急需合并时使用。",
        )
        if use_unreviewed:
            unreviewed_typed = st.text_input(
                '输入 "ALLOW_UNREVIEWED" 进一步解锁',
                key=f"pl_merge_au_text_{batch_id}",
                placeholder="ALLOW_UNREVIEWED",
            )

    unreviewed_ok = (not use_unreviewed) or (unreviewed_typed.strip() == "ALLOW_UNREVIEWED")
    unlocked = (
        dry_ok
        and confirm_chk
        and confirm_text.strip() == "确认合并"
        and typed_bid.strip() == batch_id
        and _can_unsafe_approve
        and unreviewed_ok
    )

    if st.button("⚠ 执行真实 approve 合并",
                 key="pl_merge_run", type="primary",
                 disabled=_active_task or not unlocked):
        _launch("approve_merge",
                extra={"confirmed": True,
                       "allow_unreviewed": bool(use_unreviewed and not reviewed)},
                success_msg="已启动真实 approve 合并任务")

    if not unlocked:
        miss = []
        if not dry_ok:
            miss.append("最近一次 dry-run 未成功")
        if not confirm_chk:
            miss.append('未勾选"已运行 dry-run"')
        if confirm_text.strip() != "确认合并":
            miss.append('未输入"确认合并"')
        if typed_bid.strip() != batch_id:
            miss.append("batch_id 输入不一致")
        if not _can_unsafe_approve:
            miss.append("identity_map 未命中(且未勾选无需脱敏)")
        if use_unreviewed and not unreviewed_ok:
            miss.append('未输入"ALLOW_UNREVIEWED"')
        st.caption("解锁条件未满足:" + " · ".join(miss))


_stage_card("真实 approve(approve_mentor_ingest_batch.py)",
            ["approve_merge"], body=_body_merge)


# ─── ⑥ 刷新 mentor profiles ──────────────────────────────
st.markdown("### ⑥ 刷新 mentor_profiles(可选,生成式画像)")


def _body_profiles(latest: dict | None):
    st.warning(
        "⚠ 该任务调用 DeepSeek API(付费),会重新生成所有/单个视角的画像并写入 "
        "data/mentor_knowledge/mentor_profiles.json 。建议在 approve 成功后再触发。"
    )
    cols = st.columns([1, 1, 2])
    with cols[0]:
        force = st.checkbox("--force(忽略 source_hash)",
                            value=True, key=f"pl_prof_force_{batch_id}")
    with cols[1]:
        view = st.text_input("--view(可选,仅一个视角)", value="",
                             key=f"pl_prof_view_{batch_id}",
                             placeholder="mentor:观局者")
    with cols[2]:
        confirm_text = st.text_input(
            '输入 "刷新画像" 解锁(确认会调付费 API)',
            key=f"pl_prof_text_{batch_id}", placeholder="刷新画像",
        )

    unlocked = confirm_text.strip() == "刷新画像"
    if st.button("▶ 创建并运行 generate_profiles 任务",
                 key="pl_prof_run", disabled=_active_task or not unlocked):
        extra = {"confirmed": True, "force": bool(force)}
        if view.strip():
            extra["view"] = view.strip()
        _launch("generate_profiles", extra=extra,
                success_msg="已启动 generate_profiles 任务")
    if not unlocked:
        st.caption('未输入"刷新画像",已禁用按钮。')


_stage_card("刷新画像(generate_mentor_profiles.py)",
            ["generate_profiles"], body=_body_profiles)


# ─── 任务列表 + 日志查看器 ───────────────────────────────
st.markdown("---")
st.markdown("### 本 batch 最近任务 + 日志")

_PIPELINE_TYPES = (
    "transcribe", "transcribe_dry_run", "transcribe_retry_failed",
    "build_chunks", "build_cards", "build_cards_sample", "build_cards_full",
    "approve_dry_run", "approve_merge", "generate_profiles",
)

fc1, fc2, fc3 = st.columns([2, 2, 1])
with fc1:
    type_options = ["全部"] + list(_PIPELINE_TYPES)
    sel_type = st.selectbox("过滤 task_type", type_options,
                            index=0, key="pl_filter_type")
with fc2:
    status_options = ["全部"] + list(tasksvc.TASK_STATUSES)
    sel_status = st.selectbox("过滤 status", status_options,
                              index=0, key="pl_filter_status")
with fc3:
    log_lines = st.selectbox("日志行数", [100, 300, 1000], index=0,
                             key="pl_log_lines")

_filter_type = None if sel_type == "全部" else sel_type
_filter_status = None if sel_status == "全部" else sel_status

if _filter_type is None:
    all_tasks = []
    for tt in _PIPELINE_TYPES:
        all_tasks.extend(tasksvc.list_tasks(batch_id, task_type=tt))
    all_tasks.sort(key=lambda t: t.get("created_at", ""), reverse=True)
else:
    all_tasks = tasksvc.list_tasks(batch_id, task_type=_filter_type)

if _filter_status:
    all_tasks = [t for t in all_tasks if t.get("status") == _filter_status]

if not all_tasks:
    st.caption("当前过滤条件下没有任务。")
else:
    summary = tasksvc.batch_task_summary(batch_id)
    cs = st.columns(6)
    for col, s in zip(cs, ("pending", "running", "success", "failed", "skipped", "cancelled")):
        col.metric(f"{_STATUS_ICON[s]} {s}", summary.get(s, 0))

    for t in all_tasks[:20]:
        tid = t["task_id"]
        icon = _STATUS_ICON.get(t.get("status"), "?")
        header = (f"{icon} {t.get('task_type')} · {t.get('status')} · "
                  f"progress={t.get('progress',0)} · {tid}")
        with st.expander(header,
                         expanded=(t.get("status") == "running")):
            mc = st.columns(4)
            mc[0].write(f"**status**: {t.get('status')}")
            mc[1].write(f"**progress**: {t.get('progress', 0)}")
            mc[2].write(f"**retry**: {t.get('retry_count', 0)}/{t.get('max_retries', 0)}")
            mc[3].write(f"**pid**: {t.get('pid')}")
            st.write(f"**started_at**: {t.get('started_at') or '-'} · "
                     f"**finished_at**: {t.get('finished_at') or '-'}")
            if t.get("command"):
                st.code(t["command"], language="bash")

            ext = t.get("extra") or {}
            prog = ext.get("parsed_progress") or {}
            if prog:
                with st.expander("parsed_progress(结构化进度)", expanded=False):
                    st.json(prog)

            if t.get("error"):
                st.error(t["error"][:2000])

            log_tail = tasksvc.read_log_tail(batch_id, tid, max_lines=int(log_lines))
            if log_tail:
                st.text_area("log tail", log_tail, height=240,
                             key=f"pl_log_{tid}")

            ac = st.columns(3)
            with ac[0]:
                if st.button("retry", key=f"pl_retry_{tid}",
                             disabled=t.get("status") not in ("failed", "cancelled", "skipped")):
                    tasksvc.retry_task(batch_id, tid)
                    res = tasksvc.launch_task(batch_id, tid)
                    if not res.get("ok"):
                        st.warning(res.get("error"))
                    st.rerun()
            with ac[1]:
                if st.button("skip", key=f"pl_skip_{tid}",
                             disabled=t.get("status") in ("success", "skipped")):
                    tasksvc.skip_task(batch_id, tid)
                    st.rerun()
            with ac[2]:
                if st.button("cancel", key=f"pl_cancel_{tid}",
                             disabled=t.get("status") not in ("pending", "running")):
                    tasksvc.cancel_task(batch_id, tid)
                    st.rerun()

st.caption("提示:任务在后台子进程运行,UI 不会自动 rerun。需要刷新进度时点页首「🔄 刷新页面」。")
