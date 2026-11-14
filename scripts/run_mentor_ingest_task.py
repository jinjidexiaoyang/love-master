"""
run_mentor_ingest_task — P4C 统一任务执行器(Admin Content Studio P0)

由 mentor_task_service.launch_task() 以后台子进程方式拉起,执行单个任务。
读取 task → 按 task_type dispatch → 调用现有已验证 CLI 脚本 → 流式写日志 →
更新 task status/progress/error/finished_at/pid。

用法:
  python scripts/run_mentor_ingest_task.py --batch <batch_id> --task <task_id>

dispatch:
  collect_url_metadata → scripts/collect_url_metadata.py --batch <batch_id> --url <extra.url>
  collect_bilibili_space → scripts/collect_bilibili_space.py --batch <batch_id> --url <extra.space_url> [--max-items N]
  download_video       → scripts/download_url_video.py --batch <batch_id> --item <extra.item_id> [--quality Q] [--force]
  transcribe      → scripts/transcribe_videos.py --batch <batch_id> [--retry-failed] [--backend whisper_cpp]
  transcribe_dry_run     → scripts/transcribe_videos.py --batch <batch_id> --dry-run [--backend ...]
  transcribe_retry_failed→ scripts/transcribe_videos.py --batch <batch_id> --retry-failed [--backend ...]
  build_cards     → scripts/build_ingest_cards.py --batch <batch_id> [--sample N] [--skip-cards] [--resume]
  build_chunks           → scripts/build_ingest_cards.py --batch <batch_id> --skip-cards
  build_cards_sample     → scripts/build_ingest_cards.py --batch <batch_id> --sample N [--resume]
  build_cards_full       → scripts/build_ingest_cards.py --batch <batch_id> [--resume]
  approve_dry_run → scripts/approve_mentor_ingest_batch.py --batch <batch_id> --decision approve --dry-run [--allow-unreviewed]
  approve_merge   → scripts/approve_mentor_ingest_batch.py --batch <batch_id> --decision approve [--allow-unreviewed]
                    (必须 task.extra.confirmed == True,否则拒绝执行;allow_unreviewed 必须显式 True)
  generate_profiles → scripts/generate_mentor_profiles.py [--force]
                    (必须 task.extra.confirmed == True,因为会调付费 API)
  profile_update  → P0 占位:只记录将要执行的命令,不真正调用

安全:
  - 不自动 approve;approve_merge 需 extra.confirmed=True
  - approve_*_allow_unreviewed 需 extra.allow_unreviewed=True 才会带 --allow-unreviewed
  - generate_profiles 需 extra.confirmed=True(避免误触付费 API)
  - 不直接写正式 cards/chunks/profiles(交由 approve 脚本自身的备份+合并逻辑)
  - returncode != 0 → status=failed,stderr 写入 error
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services import mentor_task_service as tasks  # noqa: E402
from services import mentor_pipeline_progress as progress  # noqa: E402
from services.mentor_ingest_service import batch_dir  # noqa: E402

PYTHON = sys.executable
SCRIPTS = ROOT / "scripts"


def _build_command(task: dict) -> tuple[list[str] | None, str | None]:
    """根据 task_type + extra 构造子命令。返回 (cmd, error)。"""
    batch_id = task["batch_id"]
    ttype = task["task_type"]
    extra = task.get("extra") or {}

    if ttype == "collect_url_metadata":
        url = (extra.get("url") or "").strip()
        if not url:
            return None, "collect_url_metadata 需要 extra.url"
        cmd = [PYTHON, str(SCRIPTS / "collect_url_metadata.py"),
               "--batch", batch_id, "--url", url]
        return cmd, None

    if ttype == "collect_bilibili_space":
        space_url = (extra.get("space_url") or extra.get("url") or "").strip()
        if not space_url:
            return None, "collect_bilibili_space 需要 extra.space_url"
        cmd = [PYTHON, str(SCRIPTS / "collect_bilibili_space.py"),
               "--batch", batch_id, "--url", space_url]
        if extra.get("max_items"):
            cmd += ["--max-items", str(int(extra["max_items"]))]
        return cmd, None

    if ttype == "download_video":
        item_id = (extra.get("item_id") or task.get("item_id") or "").strip()
        if not item_id:
            return None, "download_video 需要 extra.item_id"
        cmd = [PYTHON, str(SCRIPTS / "download_url_video.py"),
               "--batch", batch_id, "--item", item_id]
        if extra.get("quality"):
            cmd += ["--quality", str(extra["quality"])]
        if extra.get("force"):
            cmd.append("--force")
        return cmd, None

    if ttype in ("transcribe", "transcribe_dry_run", "transcribe_retry_failed"):
        cmd = [PYTHON, str(SCRIPTS / "transcribe_videos.py"), "--batch", batch_id]
        if ttype == "transcribe_dry_run" or extra.get("dry_run"):
            cmd.append("--dry-run")
        if ttype == "transcribe_retry_failed" or extra.get("retry_failed"):
            cmd.append("--retry-failed")
        if extra.get("force"):
            cmd.append("--force")
        backend = (extra.get("backend") or "").strip()
        if backend:
            cmd += ["--backend", backend]
        return cmd, None

    if ttype in ("build_cards", "build_chunks",
                 "build_cards_sample", "build_cards_full"):
        cmd = [PYTHON, str(SCRIPTS / "build_ingest_cards.py"), "--batch", batch_id]
        if ttype == "build_chunks" or extra.get("skip_cards"):
            cmd.append("--skip-cards")
        if ttype == "build_cards_sample":
            n = int(extra.get("sample") or 20)
            cmd += ["--sample", str(n), "--resume"]
        elif ttype == "build_cards_full":
            cmd.append("--resume")
        else:
            # build_cards: legacy / 自定义
            if extra.get("sample"):
                cmd += ["--sample", str(int(extra["sample"]))]
            if extra.get("resume"):
                cmd.append("--resume")
        return cmd, None

    if ttype == "approve_dry_run":
        cmd = [PYTHON, str(SCRIPTS / "approve_mentor_ingest_batch.py"),
               "--batch", batch_id, "--decision", "approve", "--dry-run"]
        if extra.get("allow_unreviewed") is True:
            cmd.append("--allow-unreviewed")
        return cmd, None

    if ttype == "approve_merge":
        if not extra.get("confirmed") is True:
            return None, "approve_merge 需要 extra.confirmed=True(二次确认),已拒绝执行"
        cmd = [PYTHON, str(SCRIPTS / "approve_mentor_ingest_batch.py"),
               "--batch", batch_id, "--decision", "approve"]
        if extra.get("allow_unreviewed") is True:
            cmd.append("--allow-unreviewed")
        return cmd, None

    if ttype == "generate_profiles":
        if not extra.get("confirmed") is True:
            return None, "generate_profiles 需要 extra.confirmed=True(会调用付费 API),已拒绝执行"
        cmd = [PYTHON, str(SCRIPTS / "generate_mentor_profiles.py")]
        if extra.get("force", True):
            cmd.append("--force")
        if extra.get("view"):
            cmd += ["--view", str(extra["view"])]
        if extra.get("limit"):
            cmd += ["--limit", str(int(extra["limit"]))]
        return cmd, None

    if ttype == "profile_update":
        # P0 占位:不真正调用,只回显命令
        return None, "PLACEHOLDER"

    return None, f"未知 task_type: {ttype}"


def _maybe_save_dry_run_snapshot(batch_id: str, task_type: str, prog: dict) -> None:
    """approve dry-run 完成后,把 dry_run_result 落到
    data/mentor_ingest/staging/batches/<batch_id>/approve_dry_run_last.json,供 UI 复读。"""
    if task_type != "approve_dry_run":
        return
    dr = prog.get("dry_run_result") or {}
    if not dr:
        return
    try:
        bd = batch_dir(batch_id)
        bd.mkdir(parents=True, exist_ok=True)
        snap = {
            "batch_id": batch_id,
            "saved_at": datetime.now().isoformat(timespec="seconds"),
            "result": dr,
        }
        (bd / "approve_dry_run_last.json").write_text(
            json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:
        pass


def _stream_run(batch_id: str, task_id: str, cmd: list[str],
                task_type: str) -> int:
    """运行子命令,实时把 stdout/stderr 行写入任务日志,并解析 parsed_progress。"""
    tasks.append_log(batch_id, task_id, f"[CMD] {' '.join(cmd)}")
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            cwd=str(ROOT),
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
    except Exception as e:
        tasks.append_log(batch_id, task_id, f"[ERROR] Popen failed: {type(e).__name__}: {e}")
        return 1

    # 初始化 parsed_progress
    cur = tasks.get_task(batch_id, task_id) or {}
    extra = dict(cur.get("extra") or {})
    prog = progress.init_progress(task_type, extra)
    prog["status"] = "running"
    extra["parsed_progress"] = prog
    tasks.update_task(batch_id, task_id, pid=proc.pid, progress=10, extra=extra)

    stage = progress.stage_for_task_type(task_type)
    last_persist = 0
    line_count = 0

    assert proc.stdout is not None
    for line in proc.stdout:
        tasks.append_log(batch_id, task_id, line.rstrip("\n"))
        line_count += 1
        prog = progress.parse_line(stage, line, prog)

        # 每 10 行或解析到关键字段(current 变化 / done)就写一次
        # 避免每行都写文件造成过多 IO
        if line_count - last_persist >= 5 or prog.get("done"):
            try:
                pct = progress.progress_percent(prog)
                cur_extra = dict((tasks.get_task(batch_id, task_id) or {}).get("extra") or {})
                cur_extra["parsed_progress"] = prog
                tasks.update_task(batch_id, task_id, progress=pct, extra=cur_extra)
            except Exception:
                pass
            last_persist = line_count

        if tasks.cancel_requested(batch_id, task_id):
            tasks.append_log(batch_id, task_id, "[CANCEL] 收到取消请求,终止子进程")
            try:
                proc.terminate()
            except Exception:
                pass
            try:
                proc.wait(timeout=10)
            except Exception:
                pass
            # final progress write
            try:
                cur_extra = dict((tasks.get_task(batch_id, task_id) or {}).get("extra") or {})
                prog["status"] = "cancelled"
                cur_extra["parsed_progress"] = prog
                tasks.update_task(batch_id, task_id, extra=cur_extra)
            except Exception:
                pass
            return 130

    proc.wait()

    # final persist + dry-run snapshot
    try:
        prog["status"] = "success" if proc.returncode == 0 else "failed"
        cur_extra = dict((tasks.get_task(batch_id, task_id) or {}).get("extra") or {})
        cur_extra["parsed_progress"] = prog
        tasks.update_task(batch_id, task_id, extra=cur_extra)
    except Exception:
        pass

    if proc.returncode == 0:
        _maybe_save_dry_run_snapshot(batch_id, task_type, prog)

    return proc.returncode


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="P4C 统一任务执行器")
    ap.add_argument("--batch", required=True)
    ap.add_argument("--task", required=True)
    args = ap.parse_args(argv)

    batch_id, task_id = args.batch, args.task
    task = tasks.get_task(batch_id, task_id)
    if not task:
        print(f"[ERROR] task 不存在: {batch_id}/{task_id}")
        return 2

    if task.get("status") == "cancelled":
        tasks.append_log(batch_id, task_id, "[SKIP] task 已 cancelled,runner 退出")
        return 0

    cmd, err = _build_command(task)

    # profile_update 占位
    if err == "PLACEHOLDER":
        placeholder = "python scripts/generate_mentor_profiles.py --force"
        tasks.mark_running(batch_id, task_id, pid=None)
        tasks.append_log(batch_id, task_id,
                         "[PLACEHOLDER] P0 不自动刷新画像。请管理员手动执行:")
        tasks.append_log(batch_id, task_id, f"  {placeholder}")
        tasks.update_task(batch_id, task_id, command=placeholder)
        tasks.mark_success(batch_id, task_id,
                           message="placeholder:请手动运行 generate_mentor_profiles.py --force")
        return 0

    # 构造失败(如 approve_merge / generate_profiles 未确认)
    if cmd is None:
        tasks.mark_running(batch_id, task_id)
        tasks.append_log(batch_id, task_id, f"[REJECT] {err}")
        tasks.mark_failed(batch_id, task_id, err or "命令构造失败")
        return 1

    tasks.mark_running(batch_id, task_id)
    tasks.update_task(batch_id, task_id, command=" ".join(cmd))
    try:
        code = _stream_run(batch_id, task_id, cmd, task["task_type"])
    except Exception as e:
        tasks.append_log(batch_id, task_id, f"[ERROR] {type(e).__name__}: {e}")
        tasks.mark_failed(batch_id, task_id, f"{type(e).__name__}: {e}")
        return 1

    if code == 130:
        tasks.update_task(batch_id, task_id, status="cancelled",
                          finished_at=tasks._now_iso(), message="cancelled by admin")
        return 130
    if code != 0:
        tail = tasks.read_log_tail(batch_id, task_id, max_lines=20)
        tasks.mark_failed(batch_id, task_id,
                          f"returncode={code}\n--- log tail ---\n{tail}")
        return code

    tasks.mark_success(batch_id, task_id, message=f"done (returncode=0)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
