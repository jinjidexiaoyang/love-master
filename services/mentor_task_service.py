"""
mentor_task_service — P4C 任务队列基础设施(Admin Content Studio P0)

职责:
  - 为 batch 内的长任务(转写 / 卡片化 / 审核合并 / 画像刷新)提供文件态任务队列
  - 状态全部落盘,不依赖 Streamlit session;UI rerun / 进程重启都能续上
  - 每个 batch 一份 tasks.json + 一个全局轻索引 tasks_index.json
  - JSON 原子写(.tmp + os.replace)+ 简单文件锁,避免 UI 与 runner 并发写竞争
  - 不调用 LLM,不碰正式 cards/chunks/profiles

任务存储:
  data/mentor_ingest/staging/batches/<batch_id>/tasks.json        # 任务清单
  data/mentor_ingest/staging/batches/<batch_id>/task_logs/<id>.log # 每任务日志
  data/mentor_ingest/tasks_index.json                              # 全局轻索引
"""
from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from services.mentor_ingest_service import INGEST_ROOT, batch_dir, STAGING_ROOT

TASKS_INDEX_PATH = INGEST_ROOT / "tasks_index.json"

TASK_STATUSES = ("pending", "running", "success", "failed", "skipped", "cancelled")
TASK_TYPES = (
    "collect_url_metadata",
    "collect_bilibili_space",
    "download_video",
    "transcribe",
    "transcribe_dry_run",
    "transcribe_retry_failed",
    "build_chunks",
    "build_cards",
    "build_cards_sample",
    "build_cards_full",
    "approve_dry_run",
    "approve_merge",
    "profile_update",
    "generate_profiles",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _new_task_id(task_type: str) -> str:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"task_{task_type}_{ts}_{uuid.uuid4().hex[:6]}"


# ─── 路径 ─────────────────────────────────────────────────
def _tasks_path(batch_id: str) -> Path:
    return batch_dir(batch_id) / "tasks.json"


def _task_logs_dir(batch_id: str) -> Path:
    return batch_dir(batch_id) / "task_logs"


def _log_path(batch_id: str, task_id: str) -> Path:
    return _task_logs_dir(batch_id) / f"{task_id}.log"


# ─── 原子写 + 简单文件锁 ──────────────────────────────────
def _atomic_write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}.{uuid.uuid4().hex[:6]}")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


class _FileLock:
    """基于 O_CREAT|O_EXCL 的简单跨进程锁(v1 串行,足够)。"""

    def __init__(self, target: Path, timeout: float = 10.0, poll: float = 0.05):
        self.lock_path = target.with_suffix(target.suffix + ".lock")
        self.timeout = timeout
        self.poll = poll
        self._fd = None

    def __enter__(self):
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.time() + self.timeout
        while True:
            try:
                self._fd = os.open(str(self.lock_path), os.O_CREAT | os.O_EXCL | os.O_RDWR)
                return self
            except FileExistsError:
                # 清理陈旧锁(>60s 视为残留)
                try:
                    if time.time() - self.lock_path.stat().st_mtime > 60:
                        os.unlink(self.lock_path)
                        continue
                except FileNotFoundError:
                    continue
                if time.time() > deadline:
                    # 超时则强制夺锁,避免永久卡死
                    try:
                        os.unlink(self.lock_path)
                    except FileNotFoundError:
                        pass
                    continue
                time.sleep(self.poll)

    def __exit__(self, *exc):
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        try:
            os.unlink(self.lock_path)
        except FileNotFoundError:
            pass


def _load_tasks(batch_id: str) -> list[dict]:
    return _read_json(_tasks_path(batch_id), []) or []


def _save_tasks(batch_id: str, tasks: list[dict]) -> None:
    _atomic_write_json(_tasks_path(batch_id), tasks)


# ─── 任务模板 ─────────────────────────────────────────────
def _new_task_record(batch_id: str, task_type: str, command, item_id, max_retries: int) -> dict:
    task_id = _new_task_id(task_type)
    now = _now_iso()
    return {
        "task_id": task_id,
        "batch_id": batch_id,
        "item_id": item_id or "",
        "task_type": task_type,
        "status": "pending",
        "progress": 0,
        "message": "",
        "command": command or "",
        "pid": None,
        "started_at": "",
        "finished_at": "",
        "error": "",
        "retry_count": 0,
        "max_retries": int(max_retries),
        "log_path": str(_log_path(batch_id, task_id)),
        "created_at": now,
        "updated_at": now,
        "cancel_requested": False,
        "extra": {},
    }


# ─── create / list / get / update ─────────────────────────
def create_task(batch_id: str, task_type: str, command: str | None = None,
                item_id: str | None = None, max_retries: int = 3,
                extra: dict | None = None) -> dict:
    if task_type not in TASK_TYPES:
        raise ValueError(f"不支持的 task_type: {task_type}")
    with _FileLock(_tasks_path(batch_id)):
        tasks = _load_tasks(batch_id)
        rec = _new_task_record(batch_id, task_type, command, item_id, max_retries)
        if extra:
            rec["extra"] = dict(extra)
        tasks.append(rec)
        _save_tasks(batch_id, tasks)
    # 预建空日志文件,保证 log_path 始终存在
    lp = _log_path(batch_id, rec["task_id"])
    lp.parent.mkdir(parents=True, exist_ok=True)
    if not lp.exists():
        lp.write_text("", encoding="utf-8")
    _touch_index(batch_id)
    return rec


def list_tasks(batch_id: str | None = None, status: str | None = None,
               task_type: str | None = None) -> list[dict]:
    if batch_id is None:
        # 跨 batch:遍历 staging 目录
        out: list[dict] = []
        if STAGING_ROOT.exists():
            for d in STAGING_ROOT.iterdir():
                if d.is_dir() and (d / "tasks.json").exists():
                    out.extend(_load_tasks(d.name))
    else:
        out = _load_tasks(batch_id)
    if status:
        out = [t for t in out if t.get("status") == status]
    if task_type:
        out = [t for t in out if t.get("task_type") == task_type]
    out.sort(key=lambda t: t.get("created_at", ""), reverse=True)
    return out


def get_task(batch_id: str, task_id: str) -> dict | None:
    for t in _load_tasks(batch_id):
        if t.get("task_id") == task_id:
            return t
    return None


def update_task(batch_id: str, task_id: str, **fields) -> dict | None:
    with _FileLock(_tasks_path(batch_id)):
        tasks = _load_tasks(batch_id)
        updated = None
        for t in tasks:
            if t.get("task_id") == task_id:
                t.update(fields)
                t["updated_at"] = _now_iso()
                updated = t
                break
        if updated is not None:
            _save_tasks(batch_id, tasks)
    if updated is not None:
        _touch_index(batch_id)
    return updated


# ─── 日志 ─────────────────────────────────────────────────
def append_log(batch_id: str, task_id: str, text: str) -> None:
    lp = _log_path(batch_id, task_id)
    lp.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%H:%M:%S")
    line = text if text.endswith("\n") else text + "\n"
    try:
        with lp.open("a", encoding="utf-8") as f:
            f.write(f"[{ts}] {line}")
    except Exception:
        pass


def read_log_tail(batch_id: str, task_id: str, max_lines: int = 200) -> str:
    lp = _log_path(batch_id, task_id)
    if not lp.exists():
        return ""
    try:
        lines = lp.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return ""
    return "\n".join(lines[-max_lines:])


# ─── 状态转换 ─────────────────────────────────────────────
def mark_running(batch_id: str, task_id: str, pid: int | None = None) -> dict | None:
    return update_task(batch_id, task_id, status="running", pid=pid,
                       started_at=_now_iso(), error="", progress=20,
                       message="running")


def mark_success(batch_id: str, task_id: str, message: str = "") -> dict | None:
    return update_task(batch_id, task_id, status="success", progress=100,
                       finished_at=_now_iso(), message=message or "success")


def mark_failed(batch_id: str, task_id: str, error: str) -> dict | None:
    return update_task(batch_id, task_id, status="failed",
                       finished_at=_now_iso(), error=str(error)[:4000],
                       message="failed")


# ─── retry / skip / cancel ────────────────────────────────
def retry_task(batch_id: str, task_id: str) -> dict | None:
    t = get_task(batch_id, task_id)
    if not t:
        return None
    if t.get("status") not in ("failed", "cancelled", "skipped"):
        return t  # 只重试已结束的非成功任务
    return update_task(
        batch_id, task_id,
        status="pending", progress=0, error="", message="re-queued",
        pid=None, started_at="", finished_at="",
        cancel_requested=False,
        retry_count=int(t.get("retry_count", 0)) + 1,
    )


def skip_task(batch_id: str, task_id: str) -> dict | None:
    t = get_task(batch_id, task_id)
    if not t:
        return None
    if t.get("status") == "running":
        # running 不直接 skip,转为请求取消
        return update_task(batch_id, task_id, cancel_requested=True,
                           message="cancel requested (skip on running)")
    return update_task(batch_id, task_id, status="skipped",
                       finished_at=_now_iso(), message="skipped by admin")


def cancel_task(batch_id: str, task_id: str) -> dict | None:
    t = get_task(batch_id, task_id)
    if not t:
        return None
    if t.get("status") == "pending":
        return update_task(batch_id, task_id, status="cancelled",
                           finished_at=_now_iso(), message="cancelled before start")
    if t.get("status") == "running":
        # 无法保证安全 kill,只写 cancel flag,由 runner 轮询自退
        append_log(batch_id, task_id, "[CANCEL] 管理员请求取消,等待 runner 响应")
        return update_task(batch_id, task_id, cancel_requested=True,
                           message="cancel requested (running)")
    return t


def retry_failed_tasks(batch_id: str) -> int:
    n = 0
    for t in list_tasks(batch_id, status="failed"):
        if retry_task(batch_id, t["task_id"]):
            n += 1
    return n


# ─── 汇总 / 串行保护 ──────────────────────────────────────
def batch_task_summary(batch_id: str) -> dict:
    tasks = _load_tasks(batch_id)
    summary = {s: 0 for s in TASK_STATUSES}
    for t in tasks:
        s = t.get("status")
        if s in summary:
            summary[s] += 1
    summary["total"] = len(tasks)
    return summary


def has_active_task(batch_id: str) -> bool:
    """v1 串行:存在 running 任务时,不允许再启动新任务。"""
    return any(t.get("status") == "running" for t in _load_tasks(batch_id))


def cancel_requested(batch_id: str, task_id: str) -> bool:
    t = get_task(batch_id, task_id)
    return bool(t and t.get("cancel_requested"))


# ─── 全局轻索引 ───────────────────────────────────────────
def _touch_index(batch_id: str) -> None:
    try:
        rebuild_tasks_index()
    except Exception:
        pass


def rebuild_tasks_index() -> dict:
    """重建全局轻索引:只存每个 batch 的状态计数 + 最近更新时间。"""
    index = {"updated_at": _now_iso(), "batches": {}}
    if STAGING_ROOT.exists():
        for d in sorted(STAGING_ROOT.iterdir()):
            if not (d.is_dir() and (d / "tasks.json").exists()):
                continue
            bid = d.name
            tasks = _load_tasks(bid)
            if not tasks:
                continue
            summary = {s: 0 for s in TASK_STATUSES}
            last_updated = ""
            for t in tasks:
                s = t.get("status")
                if s in summary:
                    summary[s] += 1
                last_updated = max(last_updated, t.get("updated_at", ""))
            summary["total"] = len(tasks)
            index["batches"][bid] = {
                "summary": summary,
                "last_updated": last_updated,
            }
    _atomic_write_json(TASKS_INDEX_PATH, index)
    return index


def load_tasks_index() -> dict:
    return _read_json(TASKS_INDEX_PATH, {"batches": {}}) or {"batches": {}}


# ─── 启动 runner(后台进程,UI 不阻塞)────────────────────
def launch_task(batch_id: str, task_id: str) -> dict:
    """
    以后台子进程方式启动 runner 执行指定任务,立即返回,不等待。
    v1 串行:若已有 running 任务则拒绝。
    """
    import subprocess
    import sys

    t = get_task(batch_id, task_id)
    if not t:
        return {"ok": False, "error": f"task 不存在: {task_id}"}
    if t.get("status") not in ("pending",):
        return {"ok": False, "error": f"task 状态非 pending,当前={t.get('status')}"}
    if has_active_task(batch_id):
        return {"ok": False, "error": "已有 running 任务,v1 串行执行,请等待完成"}

    runner = Path(__file__).resolve().parent.parent / "scripts" / "run_mentor_ingest_task.py"
    cmd = [sys.executable, str(runner), "--batch", batch_id, "--task", task_id]
    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            cwd=str(Path(__file__).resolve().parent.parent),
            creationflags=creationflags,
        )
    except Exception as e:
        mark_failed(batch_id, task_id, f"launch failed: {type(e).__name__}: {e}")
        return {"ok": False, "error": str(e)}
    append_log(batch_id, task_id, f"[LAUNCH] pid={proc.pid} cmd={' '.join(cmd)}")
    update_task(batch_id, task_id, pid=proc.pid, message="launched")
    return {"ok": True, "pid": proc.pid, "task_id": task_id}


def create_and_launch(batch_id: str, task_type: str, command: str | None = None,
                      item_id: str | None = None, max_retries: int = 3,
                      extra: dict | None = None) -> dict:
    if has_active_task(batch_id):
        return {"ok": False, "error": "已有 running 任务,v1 串行执行,请等待完成"}
    rec = create_task(batch_id, task_type, command=command, item_id=item_id,
                      max_retries=max_retries, extra=extra)
    res = launch_task(batch_id, rec["task_id"])
    res["task"] = get_task(batch_id, rec["task_id"])
    return res
