"""
mentor_pipeline_progress — 解析采集流水线脚本 stdout,生成结构化 parsed_progress

只读、纯函数,无副作用。供 runner 在每行 stdout 上调用,把解析结果写入 task.extra.parsed_progress。

支持的脚本输出形态(取自实际日志):

  transcribe_videos.py
    [DRY-RUN] 待转写 297 项 backend=whisper_cpp:
    [282/297] item_0282_xxx kind=video backend=whisper_cpp title=...
    [DONE] backend=whisper_cpp ok=297 fail=0 skip=0 (19335.8s)

  build_ingest_cards.py
    [OK] chunks 2833 → data\mentor_ingest\staging\batches\<bid>\chunks.json
    [2806/2833] mentor_xxx_chunk_0005 (711 字)
    [DONE] cards accepted=2771 rejected=62 (13132.8s)

  approve_mentor_ingest_batch.py
    [DRY-RUN] 合并预览: {'ok': True, 'card_source': '...', ...}
    [OK] batch <bid> 已合并到正式库
    [OK] {'ok': True, 'merged_chunks': 2833, ...}

  generate_mentor_profiles.py
    [INFO] cards 共 3663 张
    [INFO] 待生成视角 5 个
    [1/5] mentor:观局者 (cards=2871)
    [OK] ok=5, skipped=0, errors=0
"""
from __future__ import annotations

import ast
import re

# ── transcribe_videos.py ──────────────────────────────────
_RE_TRANSCRIBE_DRY = re.compile(r"\[DRY-RUN\].*?待转写\s+(\d+)\s+项\s+backend=(\S+)")
_RE_TRANSCRIBE_ITEM = re.compile(
    r"^\[(\d+)/(\d+)\]\s+(\S+)\s+kind=(\S+)\s+backend=(\S+)"
)
_RE_TRANSCRIBE_DONE = re.compile(
    r"\[DONE\]\s+backend=(\S+)\s+ok=(\d+)\s+fail=(\d+)\s+skip=(\d+)\s+\(([\d.]+)s\)"
)

# ── build_ingest_cards.py ─────────────────────────────────
_RE_CHUNKS_OK = re.compile(r"\[OK\]\s+chunks\s+(\d+)\s+(?:→|->)\s+(.+?)\s*$")
_RE_CARDS_ITEM = re.compile(r"^\[(\d+)/(\d+)\]\s+(\S+)\s+\((\d+)\s*字\)")
_RE_CARDS_DONE = re.compile(
    r"\[DONE\]\s+cards\s+accepted=(\d+)\s+rejected=(\d+)\s+\(([\d.]+)s\)"
)

# ── approve_mentor_ingest_batch.py ────────────────────────
_RE_APPROVE_DRY = re.compile(r"\[DRY-RUN\]\s+合并预览[::]\s*(\{.*\})\s*$")
_RE_APPROVE_OK_BATCH = re.compile(r"\[OK\]\s+batch\s+(\S+)\s+已合并到正式库")
_RE_APPROVE_OK_DICT = re.compile(r"\[OK\]\s+(\{.*\})\s*$")
_RE_APPROVE_REJECT = re.compile(r"\[ERROR\]\s+(.+)")

_APPROVE_KEYS = (
    "ok", "reviewed_mode", "card_source",
    "draft_chunks", "cards_to_merge", "draft_rejected",
    "merged_chunks", "merged_cards",
    "skip_chunks_duplicate", "skip_cards_excluded",
    "skip_cards_chunk_dup", "skip_cards_url_dup",
    "skip_cards_fingerprint_dup",
    "dry_run",
)

# ── generate_mentor_profiles.py ──────────────────────────
_RE_PROFILE_TOTAL = re.compile(r"\[INFO\]\s+cards\s+共\s+(\d+)\s+张")
_RE_PROFILE_VIEWS = re.compile(r"\[INFO\]\s+待生成视角\s+(\d+)\s+个")
_RE_PROFILE_VIEW = re.compile(r"^\[(\d+)/(\d+)\]\s+(\S+)\s+\(cards=(\d+)\)")
_RE_PROFILE_DONE = re.compile(
    r"\[OK\]\s+ok=(\d+),\s*skipped=(\d+),\s*errors=(\d+)"
)


def stage_for_task_type(task_type: str) -> str:
    """task_type → 进度解析阶段 key。未知类型回 task_type 本身,parser 会忽略。"""
    return {
        "transcribe": "transcribe",
        "build_cards": "build_cards",
        "approve_dry_run": "approve_dry_run",
        "approve_merge": "approve_merge",
        "generate_profiles": "generate_profiles",
    }.get(task_type, task_type or "")


def init_progress(task_type: str, extra: dict | None = None) -> dict:
    """初始化 parsed_progress。"""
    p: dict = {
        "stage": stage_for_task_type(task_type),
        "task_type": task_type,
        "status": "pending",
        "current": 0,
        "total": 0,
        "ok": 0,
        "fail": 0,
        "skip": 0,
        "current_id": "",
        "last_line": "",
        "done": False,
    }
    extra = extra or {}
    if extra.get("dry_run"):
        p["dry_run"] = True
    if extra.get("backend"):
        p["backend"] = str(extra["backend"])
    if extra.get("allow_unreviewed"):
        p["allow_unreviewed"] = True
    return p


def parse_line(stage: str, line: str, prog: dict | None = None) -> dict:
    """根据 stage 解析单行 stdout,把抽取到的字段就地写入 prog,返回 prog。

    prog 至少包含 last_line 字段;不同 stage 还会写入对应进度字段。
    解析不识别的行只更新 last_line,不抛异常。
    """
    prog = prog if prog is not None else {}
    raw = (line or "").rstrip("\r\n")
    if raw:
        prog["last_line"] = raw[:500]

    if not raw:
        return prog

    if stage == "transcribe":
        m = _RE_TRANSCRIBE_ITEM.match(raw)
        if m:
            prog.update({
                "current": int(m.group(1)),
                "total": int(m.group(2)),
                "current_id": m.group(3),
                "kind": m.group(4),
                "backend": m.group(5),
            })
            return prog
        m = _RE_TRANSCRIBE_DONE.search(raw)
        if m:
            prog.update({
                "backend": m.group(1),
                "ok": int(m.group(2)),
                "fail": int(m.group(3)),
                "skip": int(m.group(4)),
                "duration_sec": float(m.group(5)),
                "done": True,
            })
            return prog
        m = _RE_TRANSCRIBE_DRY.search(raw)
        if m:
            prog.update({
                "total": int(m.group(1)),
                "backend": m.group(2),
                "dry_run": True,
            })
            return prog
        return prog

    if stage == "build_cards":
        m = _RE_CHUNKS_OK.search(raw)
        if m:
            prog["chunks_count"] = int(m.group(1))
            prog["chunks_path"] = m.group(2).strip()
            return prog
        m = _RE_CARDS_ITEM.match(raw)
        if m:
            prog.update({
                "current": int(m.group(1)),
                "total": int(m.group(2)),
                "current_id": m.group(3),
                "char_count": int(m.group(4)),
            })
            return prog
        m = _RE_CARDS_DONE.search(raw)
        if m:
            prog.update({
                "accepted": int(m.group(1)),
                "rejected": int(m.group(2)),
                "duration_sec": float(m.group(3)),
                "done": True,
            })
            return prog
        return prog

    if stage in ("approve_dry_run", "approve_merge"):
        m = _RE_APPROVE_DRY.search(raw)
        if m:
            d = _safe_literal_dict(m.group(1))
            if d:
                prog["dry_run_result"] = d
                for k in _APPROVE_KEYS:
                    if k in d:
                        prog[k] = d[k]
                prog["dry_run"] = True
            return prog
        m = _RE_APPROVE_OK_BATCH.search(raw)
        if m:
            prog["merged_to_official"] = True
            prog["merged_batch_id"] = m.group(1)
            return prog
        m = _RE_APPROVE_OK_DICT.search(raw)
        if m:
            d = _safe_literal_dict(m.group(1))
            if d:
                prog["merge_result"] = d
                for k in _APPROVE_KEYS:
                    if k in d:
                        prog[k] = d[k]
                bk = d.get("backup")
                if isinstance(bk, dict):
                    prog["backup_dir"] = (
                        bk.get("backup_dir") or bk.get("dir") or bk.get("path") or ""
                    )
                prog["done"] = True
            return prog
        m = _RE_APPROVE_REJECT.search(raw)
        if m:
            prog["error_summary"] = m.group(1)[:500]
        return prog

    if stage == "generate_profiles":
        m = _RE_PROFILE_TOTAL.search(raw)
        if m:
            prog["total_cards"] = int(m.group(1))
            return prog
        m = _RE_PROFILE_VIEWS.search(raw)
        if m:
            n = int(m.group(1))
            prog["view_count"] = n
            prog["total"] = n
            return prog
        m = _RE_PROFILE_VIEW.match(raw)
        if m:
            prog.update({
                "current": int(m.group(1)),
                "total": int(m.group(2)),
                "current_view": m.group(3),
                "current_view_cards": int(m.group(4)),
            })
            return prog
        m = _RE_PROFILE_DONE.search(raw)
        if m:
            prog.update({
                "ok": int(m.group(1)),
                "skipped": int(m.group(2)),
                "errors": int(m.group(3)),
                "done": True,
            })
            return prog
        return prog

    return prog


def _safe_literal_dict(s: str) -> dict | None:
    """安全把 Python 风格 dict 字符串还原成 dict。失败返回 None。"""
    s = (s or "").strip()
    if not (s.startswith("{") and s.endswith("}")):
        return None
    try:
        v = ast.literal_eval(s)
        return v if isinstance(v, dict) else None
    except Exception:
        return None


def progress_percent(prog: dict) -> int:
    """从 parsed_progress 推一个 0-100 百分比,主要给 task.progress 字段用。"""
    if not prog:
        return 0
    if prog.get("done"):
        return 100
    cur = int(prog.get("current") or 0)
    tot = int(prog.get("total") or 0)
    if tot > 0:
        pct = int(round(cur * 100.0 / tot))
        return max(0, min(99, pct))
    return 0
