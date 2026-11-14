"""
P4C ASR 转写(CLI)。
对一个 batch 内 status in {downloaded, audio_extracted} 的视频/音频项做 ASR 转写。
- 视频: ffmpeg → wav (16k mono) → ASR → txt
- 音频: 直接 ASR
- 已存在 transcript_path 跳过(--force 时不跳)
- 输出写到 batch_dir/audio/ 和 batch_dir/transcripts/
- 全程不污染正式 data/mentor_knowledge/

支持两种 ASR 后端(在 admin_settings.json tools.asr_backend 切换):
- openai_whisper: 默认/fallback,OpenAI Whisper Python CLI(PyTorch,纯 CPU 慢)
- whisper_cpp:   whisper.cpp(Windows Vulkan release,AMD/Intel/NVIDIA GPU 通用)

用法:
  python scripts/transcribe_videos.py --batch <batch_id>
  python scripts/transcribe_videos.py --batch <batch_id> --item <item_id>
  python scripts/transcribe_videos.py --batch <batch_id> --backend whisper_cpp
  python scripts/transcribe_videos.py --batch <batch_id> --dry-run
  python scripts/transcribe_videos.py --batch <batch_id> --retry-failed
  python scripts/transcribe_videos.py --batch <batch_id> --force
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.mentor_ingest_service import (  # noqa: E402
    batch_dir,
    load_manifest,
    load_settings,
    load_source_items,
    save_source_items,
    save_manifest,
    set_status,
    update_item,
    write_ingest_report,
)


def _run(cmd: list[str], timeout: int = 3600) -> tuple[int, str, str]:
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        return r.returncode, r.stdout or "", r.stderr or ""
    except FileNotFoundError as e:
        return 127, "", f"FileNotFoundError: {e}"
    except subprocess.TimeoutExpired as e:
        out = ""
        err = f"timeout: {e}"
        try:
            if getattr(e, "stdout", None):
                so = e.stdout
                if isinstance(so, bytes):
                    so = so.decode("utf-8", errors="replace")
                out = so[-2000:]
            if getattr(e, "stderr", None):
                se = e.stderr
                if isinstance(se, bytes):
                    se = se.decode("utf-8", errors="replace")
                err = (err + "\n" + se)[-4000:]
        except Exception:
            pass
        return 124, out, err
    except UnicodeDecodeError as e:
        return 1, "", f"UnicodeDecodeError: {e}"
    except Exception as e:
        return 1, "", f"{type(e).__name__}: {e}"


def extract_audio(ffmpeg_exe: str, video_path: Path, audio_path: Path) -> tuple[bool, str]:
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    if audio_path.exists():
        return True, "audio exists"
    cmd = [
        ffmpeg_exe, "-y", "-loglevel", "error",
        "-i", str(video_path),
        "-vn", "-ac", "1", "-ar", "16000",
        "-f", "wav", str(audio_path),
    ]
    code, _, err = _run(cmd, timeout=1800)
    if code != 0:
        return False, f"ffmpeg failed (code={code}): {err.strip()[:300]}"
    return True, ""


def whisper_transcribe(
    whisper_exe: str,
    audio_path: Path,
    out_dir: Path,
    model: str,
    language: str,
    also_srt: bool,
) -> tuple[bool, dict, Path | None, Path | None]:
    """
    OpenAI Whisper CLI(PyTorch)。fallback 后端。

    成功:返回 (True, {}, txt_path, srt_path_or_None)
    失败:返回 (False, {cmd, returncode, stdout, stderr}, None, None)

    实现细节:
      whisper CLI 的 --output_format 只接受单值 token,合法值为
      txt/vtt/srt/tsv/json/all。本脚本用 'all' 一次产出多种格式,
      也可通过 also_srt=False 退化为 'txt'(更省磁盘,不出 srt)。
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    output_format = "all" if also_srt else "txt"
    cmd = [
        whisper_exe, str(audio_path),
        "--model", model,
        "--language", language,
        "--output_dir", str(out_dir),
        "--output_format", output_format,
        "--verbose", "False",
    ]
    code, out, err = _run(cmd, timeout=7200)
    txt_path = out_dir / (audio_path.stem + ".txt")
    srt_path = out_dir / (audio_path.stem + ".srt")
    if code != 0 or not txt_path.exists():
        return False, {
            "cmd": " ".join(cmd),
            "returncode": code,
            "stdout": (out or "")[:1000],
            "stderr": (err or "")[:2000],
            "txt_exists": txt_path.exists(),
        }, None, None
    return True, {}, txt_path, (srt_path if srt_path.exists() else None)


def whisper_cpp_transcribe(
    whisper_cpp_exe: str,
    model_path: str,
    audio_path: Path,
    out_dir: Path,
    language: str,
    extra_args: list[str] | None,
) -> tuple[bool, dict, Path | None, Path | None]:
    """
    whisper.cpp(whisper-cli.exe,Windows Vulkan release,AMD GPU 走这里)。

    whisper.cpp 默认输出与 -of 前缀同名的 .txt(开 -otxt)。本脚本传
    -of <out_dir>/<stem>,只开 -otxt,与 OpenAI Whisper 输出名兼容。
    成功:返回 (True, {}, txt_path, None)  # 不再产出 srt
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    out_prefix = out_dir / audio_path.stem
    cmd = [
        whisper_cpp_exe,
        "-m", str(model_path),
        "-f", str(audio_path),
        "-l", language or "zh",
        "-of", str(out_prefix),
        "-otxt",
        "-pp",
    ]
    if extra_args:
        cmd.extend(str(a) for a in extra_args)
    code, out, err = _run(cmd, timeout=7200)
    txt_path = out_dir / (audio_path.stem + ".txt")
    if code != 0 or not txt_path.exists():
        return False, {
            "cmd": " ".join(cmd),
            "returncode": code,
            "stdout": (out or "")[:1000],
            "stderr": (err or "")[:2000],
            "txt_exists": txt_path.exists(),
        }, None, None
    return True, {}, txt_path, None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="P4C 批次 ASR 转写")
    ap.add_argument("--batch", required=True, help="batch_id")
    ap.add_argument("--item", default=None, help="仅处理单个 item_id")
    ap.add_argument("--backend", default=None,
                    choices=["openai_whisper", "whisper_cpp"],
                    help="ASR 后端,覆盖 settings.tools.asr_backend")
    ap.add_argument("--model", default=None, help="whisper 模型(覆盖 settings)")
    ap.add_argument("--language", default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--retry-failed", action="store_true",
                    help="把 status=failed 的项重新加入待转写队列")
    ap.add_argument("--force", action="store_true",
                    help="忽略 status,只要有 local_video_path/local_audio_path 就重转")
    args = ap.parse_args(argv)

    settings = load_settings()
    tools = settings.get("tools", {})
    ffmpeg_exe = tools.get("ffmpeg_exe", "")
    backend = (args.backend or tools.get("asr_backend") or "openai_whisper").strip()
    if backend not in ("openai_whisper", "whisper_cpp"):
        print(f"[ERROR] 未知 asr_backend: {backend}")
        return 2

    # OpenAI Whisper(fallback)
    whisper_exe = tools.get("whisper_exe", "")
    ow_model = args.model or tools.get("whisper_model", "small")
    ow_language = args.language or tools.get("whisper_language", "Chinese")
    also_srt = bool(settings.get("ingest", {}).get("transcript_also_srt", True))

    # whisper.cpp(AMD GPU Vulkan)
    wc_exe = tools.get("whisper_cpp_exe", "")
    wc_model = args.model or tools.get("whisper_cpp_model", "")
    wc_language = args.language or tools.get("whisper_cpp_language", "zh")
    wc_extra = list(tools.get("whisper_cpp_extra_args") or [])

    m = load_manifest(args.batch)
    if not m:
        print(f"[ERROR] batch 不存在: {args.batch}")
        return 2

    items = load_source_items(args.batch)
    bd = batch_dir(args.batch)
    audio_dir = bd / "audio"
    trans_dir = bd / "transcripts"

    # 筛选目标
    targets = [it for it in items if (args.item is None or it["item_id"] == args.item)]
    targets = [it for it in targets if it.get("kind") in ("video", "audio")]

    if args.force:
        # 只要有源文件就重转,忽略 status
        targets = [it for it in targets
                   if it.get("local_video_path") or it.get("local_audio_path")]
    else:
        accepted_status = {"downloaded", "audio_extracted"}
        if args.retry_failed:
            accepted_status.add("failed")
        targets = [it for it in targets if it.get("status") in accepted_status]

    if not targets:
        print("[INFO] 没有待转写的 item(可能已全部转写或类型为 text/homepage)")
        print("[INFO] 提示:--retry-failed 处理失败项;--force 强制重转所有源文件项")
        return 0

    if args.dry_run:
        mode_note = []
        if args.force: mode_note.append("FORCE")
        if args.retry_failed: mode_note.append("RETRY_FAILED")
        mode_str = (" [" + ", ".join(mode_note) + "]") if mode_note else ""
        print(f"[DRY-RUN]{mode_str} 待转写 {len(targets)} 项 backend={backend}:")
        for it in targets:
            src = it.get("local_video_path") or it.get("local_audio_path")
            print(f"  · {it['item_id']} kind={it['kind']} status={it.get('status')} src={src}")
        print(f"[DRY-RUN] ffmpeg={ffmpeg_exe}")
        if backend == "whisper_cpp":
            print(f"[DRY-RUN] whisper_cpp_exe={wc_exe}")
            print(f"[DRY-RUN] whisper_cpp_model={wc_model} language={wc_language} "
                  f"extra_args={wc_extra}")
        else:
            print(f"[DRY-RUN] whisper_exe={whisper_exe} model={ow_model} "
                  f"language={ow_language} output_format={'all' if also_srt else 'txt'}")
        print(f"[DRY-RUN] transcripts_out={trans_dir}")
        return 0

    if not Path(ffmpeg_exe).exists():
        print(f"[ERROR] ffmpeg 不存在: {ffmpeg_exe}")
        return 2

    if backend == "whisper_cpp":
        if not wc_exe or not Path(wc_exe).exists():
            print(f"[ERROR] whisper_cpp_exe 不存在: {wc_exe}")
            print("        请下载 whisper.cpp Windows Vulkan release(whisper-cli.exe),"
                  "并在 admin_settings.json tools.whisper_cpp_exe 配置绝对路径")
            return 2
        if not wc_model or not Path(wc_model).exists():
            print(f"[ERROR] whisper_cpp_model 不存在: {wc_model}")
            print("        请下载 ggml-small.bin 或 ggml-medium.bin,"
                  "并在 admin_settings.json tools.whisper_cpp_model 配置绝对路径")
            return 2
    else:
        if not Path(whisper_exe).exists():
            print(f"[ERROR] whisper 不存在: {whisper_exe}")
            return 2

    set_status(args.batch, "transcribing")
    started = time.time()
    ok_cnt = 0
    fail_cnt = 0
    skip_cnt = 0
    item_logs: list[dict] = []

    for i, it in enumerate(targets, 1):
        item_id = it["item_id"]
        kind = it["kind"]
        print(f"[{i}/{len(targets)}] {item_id} kind={kind} backend={backend} "
              f"title={it.get('title','')[:30]}", flush=True)

        # 跳过已转写(--force 不跳)
        if not args.force:
            if it.get("transcript_path") and Path(it["transcript_path"]).exists():
                print(f"  · skip (transcript 已存在)")
                skip_cnt += 1
                continue

        # 1) 准备音频
        audio_path: Path | None = None
        if kind == "audio" and it.get("local_audio_path"):
            audio_path = Path(it["local_audio_path"])
        else:
            video_path = Path(it.get("local_video_path") or "")
            if not video_path.exists():
                msg = f"video 文件不存在: {video_path}"
                update_item(args.batch, item_id, status="failed", error=msg)
                item_logs.append({"item_id": item_id, "ok": False, "err": msg})
                fail_cnt += 1
                continue
            audio_path = audio_dir / (video_path.stem + ".wav")
            ok, err = extract_audio(ffmpeg_exe, video_path, audio_path)
            if not ok:
                update_item(args.batch, item_id, status="failed", error=err)
                item_logs.append({"item_id": item_id, "ok": False, "err": err})
                fail_cnt += 1
                continue
            update_item(args.batch, item_id,
                        status="audio_extracted",
                        local_audio_path=str(audio_path),
                        error="")

        # 2) ASR
        if backend == "whisper_cpp":
            ok, diag, txt_path, srt_path = whisper_cpp_transcribe(
                wc_exe, wc_model, audio_path, trans_dir, wc_language, wc_extra,
            )
        else:
            ok, diag, txt_path, srt_path = whisper_transcribe(
                whisper_exe, audio_path, trans_dir, ow_model, ow_language, also_srt,
            )
        if not ok:
            err_str = json.dumps(diag, ensure_ascii=False)
            update_item(args.batch, item_id, status="failed", error=err_str)
            item_logs.append({"item_id": item_id, "ok": False, "err": diag})
            fail_cnt += 1
            continue

        update_item(args.batch, item_id,
                    status="transcribed",
                    transcript_path=str(txt_path),
                    transcript_srt_path=str(srt_path) if srt_path else "",
                    error="")
        item_logs.append({"item_id": item_id, "ok": True,
                          "transcript_path": str(txt_path)})
        ok_cnt += 1

    duration = round(time.time() - started, 1)
    items = load_source_items(args.batch)
    transcribed_total = sum(1 for it in items if it.get("status") == "transcribed")
    m = load_manifest(args.batch)
    m["stats"]["transcribed"] = transcribed_total
    save_manifest(args.batch, m)
    set_status(args.batch, "transcribed",
               note=f"backend={backend} ok={ok_cnt} fail={fail_cnt} skip={skip_cnt} ({duration}s)")

    write_ingest_report(args.batch, {
        "phase": "transcribe",
        "backend": backend,
        "model": wc_model if backend == "whisper_cpp" else ow_model,
        "language": wc_language if backend == "whisper_cpp" else ow_language,
        "ok": ok_cnt, "failed": fail_cnt, "skipped": skip_cnt,
        "duration_sec": duration,
        "items": item_logs,
    })

    print(f"[DONE] backend={backend} ok={ok_cnt} fail={fail_cnt} skip={skip_cnt} ({duration}s)")
    return 0 if fail_cnt == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
