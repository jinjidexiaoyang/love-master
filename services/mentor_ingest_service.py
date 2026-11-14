"""
mentor_ingest_service — 导师采集与 staging 管理(P4C 后台)

职责:
  - 读写 data/mentor_ingest 下的 admin_settings / batch manifest / source_items
  - 本地文件夹扫描(视频/音频/文本)生成 source_items
  - 单个 URL / 抖音主页 / B站主页采集器接口(默认占位 + 优雅失败)
  - batch 状态机推进
  - 不调用 LLM,不修改正式 cards.json/chunks.json/mentor_profiles.json
  - 不绕过平台风控,不破解 DRM,不处理私密/付费/无权限内容
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

INGEST_ROOT = Path("data/mentor_ingest")
ADMIN_CONFIG_PATH = INGEST_ROOT / "admin_config" / "admin_settings.json"
IDENTITY_PATH = INGEST_ROOT / "admin_config" / "mentor_identity_map.json"
RAW_LOCAL = INGEST_ROOT / "raw_input" / "local"
RAW_DOUYIN = INGEST_ROOT / "raw_input" / "douyin"
RAW_BILI = INGEST_ROOT / "raw_input" / "bilibili"
STAGING_ROOT = INGEST_ROOT / "staging" / "batches"
APPROVED_ROOT = INGEST_ROOT / "approved"
LOGS_ROOT = INGEST_ROOT / "logs"

# ─── 状态机 ───────────────────────────────────────────────
BATCH_STATUSES = (
    "scanning",      # 采集中
    "scanned",       # 已生成 source_items,待管理员确认是否处理
    "transcribing",  # 转写中
    "transcribed",   # 转写完成
    "chunking",      # 切块/卡片化中
    "carded",        # 已生成 draft_cards
    "review",        # 等待管理员审核
    "approved",      # 已合并到正式库
    "rejected",      # 管理员驳回
    "failed",        # 流程失败
    "archived",      # 仅归档,不入库
)

ITEM_STATUSES = (
    "pending",
    "downloaded",
    "audio_extracted",
    "transcribed",
    "skipped",
    "failed",
)

DEFAULT_SETTINGS = {
    "version": 1,
    "tools": {
        "ffmpeg_exe": "D:/tools/ffmpeg/bin/ffmpeg.exe",
        # ASR 后端: "openai_whisper"(原 PyTorch CLI, fallback) / "whisper_cpp"(whisper.cpp Vulkan)
        "asr_backend": "openai_whisper",
        # OpenAI Whisper CLI 路径(纯 PyTorch,默认 CPU,长视频很慢,作为 fallback)
        "whisper_exe": "D:/tools/Python311/Scripts/whisper.exe",
        "whisper_model": "small",
        "whisper_language": "Chinese",
        # whisper.cpp Vulkan(AMD GPU 走这里,whisper-cli.exe 放下方路径,模型 .bin 同理)
        "whisper_cpp_exe": "D:/tools/whisper.cpp/whisper-cli.exe",
        "whisper_cpp_model": "D:/tools/whisper.cpp/models/ggml-small.bin",
        "whisper_cpp_language": "zh",
        "whisper_cpp_extra_args": [],
        "yt_dlp_exe": "",
        "bilibili_downloader_exe": "",
        "cookies_file": "",
    },
    "ingest": {
        "default_target_mode": "archive_only",
        "auto_ingest_root": "D:/raw/mentor_auto_ingest",
        "scan_extensions": {
            "video": [".mp4", ".mov", ".mkv", ".webm", ".flv"],
            "audio": [".mp3", ".m4a", ".wav", ".aac", ".flac"],
            "text":  [".txt", ".srt", ".vtt", ".md"],
        },
        "transcript_format": "txt",
        "transcript_also_srt": True,
        "auto_chunk_after_transcribe": False,
        "bilibili_max_items": 20,
    },
    "policy": {
        "respect_platform_terms": True,
        "skip_drm_or_paid": True,
        "skip_private_or_unauthorized": True,
        "graceful_failure_message": "自动采集失败,请改用本地文件夹导入兜底",
        "douyin_collector_enabled": False,
        "bilibili_collector_enabled": False,
    },
}


# ─── admin settings ───────────────────────────────────────
def load_settings() -> dict:
    if not ADMIN_CONFIG_PATH.exists():
        save_settings(DEFAULT_SETTINGS)
        return dict(DEFAULT_SETTINGS)
    try:
        data = json.loads(ADMIN_CONFIG_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return dict(DEFAULT_SETTINGS)
        # 浅层补默认
        for k, v in DEFAULT_SETTINGS.items():
            if k not in data:
                data[k] = v
        return data
    except Exception:
        return dict(DEFAULT_SETTINGS)


def save_settings(settings: dict) -> None:
    ADMIN_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    ADMIN_CONFIG_PATH.write_text(
        json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ─── 工具函数 ─────────────────────────────────────────────
_SAFE_RE = re.compile(r"[^A-Za-z0-9一-鿿_\-]")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _slug(s: str, max_len: int = 30) -> str:
    s = _SAFE_RE.sub("_", s or "")
    s = re.sub(r"_+", "_", s).strip("_")
    return s[:max_len] or "x"


def _file_hash(path: Path, chunk_size: int = 1 << 20) -> str:
    """sha1 文件 hash(只读首尾各 4MB,大文件提速,已足够去重)"""
    if not path.exists() or not path.is_file():
        return ""
    h = hashlib.sha1()
    try:
        size = path.stat().st_size
        with path.open("rb") as f:
            head = f.read(min(size, 4 * 1024 * 1024))
            h.update(head)
            if size > 8 * 1024 * 1024:
                f.seek(-4 * 1024 * 1024, 2)
                h.update(f.read())
        h.update(str(size).encode())
    except Exception:
        return ""
    return f"sha1:{h.hexdigest()[:16]}"


def make_batch_id(source_type: str, label: str = "") -> str:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    parts = ["batch", ts, _slug(source_type, 16)]
    if label:
        parts.append(_slug(label, 20))
    return "_".join(parts)


def batch_dir(batch_id: str) -> Path:
    return STAGING_ROOT / batch_id


# ─── manifest / source_items I/O ──────────────────────────
def _read_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_manifest(batch_id: str) -> dict:
    return _read_json(batch_dir(batch_id) / "manifest.json", {}) or {}


def save_manifest(batch_id: str, manifest: dict) -> None:
    _write_json(batch_dir(batch_id) / "manifest.json", manifest)


def load_source_items(batch_id: str) -> list[dict]:
    return _read_json(batch_dir(batch_id) / "source_items.json", []) or []


def save_source_items(batch_id: str, items: list[dict]) -> None:
    _write_json(batch_dir(batch_id) / "source_items.json", items)


def update_item(batch_id: str, item_id: str, **fields) -> bool:
    items = load_source_items(batch_id)
    changed = False
    for it in items:
        if it.get("item_id") == item_id:
            it.update(fields)
            changed = True
            break
    if changed:
        save_source_items(batch_id, items)
    return changed


def set_status(batch_id: str, status: str, note: str = "") -> None:
    m = load_manifest(batch_id)
    if not m:
        return
    m["status"] = status
    m["updated_at"] = _now_iso()
    history = m.setdefault("status_history", [])
    history.append({"status": status, "at": m["updated_at"], "note": note})
    save_manifest(batch_id, m)


def list_batches() -> list[dict]:
    if not STAGING_ROOT.exists():
        return []
    out = []
    for d in sorted(STAGING_ROOT.iterdir(), reverse=True):
        if not d.is_dir():
            continue
        m = _read_json(d / "manifest.json", None)
        if not m:
            continue
        out.append(m)
    return out


# ─── 创建 batch ───────────────────────────────────────────
def create_batch(
    source_type: str,
    *,
    source_url: str = "",
    local_path: str = "",
    target_mode: str = "archive_only",
    real_mentor_name: str = "",
    public_mentor_id: str = "",
    public_name: str = "",
    style_label: str = "",
    target_course: str = "",
    label: str = "",
) -> dict:
    """
    创建一个空 batch(只有 manifest,无 items)。
    target_mode: new_mentor / merge_existing_mentor / merge_existing_course / archive_only
    """
    if source_type not in ("local_folder", "single_url", "douyin_homepage", "bilibili_homepage"):
        raise ValueError(f"不支持的 source_type: {source_type}")
    if target_mode not in ("new_mentor", "merge_existing_mentor", "merge_existing_course", "archive_only"):
        raise ValueError(f"不支持的 target_mode: {target_mode}")

    batch_id = make_batch_id(source_type, label or real_mentor_name or public_name)
    bd = batch_dir(batch_id)
    for sub in ("videos", "audio", "transcripts", "cleaned"):
        (bd / sub).mkdir(parents=True, exist_ok=True)

    manifest = {
        "batch_id": batch_id,
        "source_type": source_type,
        "source_url": source_url,
        "local_path": local_path,
        "target_mode": target_mode,
        "real_mentor_name": real_mentor_name,
        "public_mentor_id": public_mentor_id,
        "public_name": public_name,
        "style_label": style_label,
        "target_course": target_course,
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        "status": "scanning",
        "status_history": [{"status": "scanning", "at": _now_iso(), "note": "batch created"}],
        "stats": {"item_count": 0, "transcribed": 0, "carded": 0},
    }
    save_manifest(batch_id, manifest)
    save_source_items(batch_id, [])
    return manifest


# ─── 本地文件夹扫描 ───────────────────────────────────────
def _classify_ext(ext: str, settings: dict) -> str:
    ext = ext.lower()
    cfg = settings.get("ingest", {}).get("scan_extensions", {})
    if ext in [e.lower() for e in cfg.get("video", [])]:
        return "video"
    if ext in [e.lower() for e in cfg.get("audio", [])]:
        return "audio"
    if ext in [e.lower() for e in cfg.get("text", [])]:
        return "text"
    return ""


def _read_companion_metadata(media_path: Path) -> dict:
    """读取同名 .json metadata(可选)。"""
    meta_path = media_path.with_suffix(media_path.suffix + ".json")
    if not meta_path.exists():
        meta_path = media_path.with_suffix(".json")
    if meta_path.exists():
        try:
            data = json.loads(meta_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            return {}
    return {}


def scan_local_folder(batch_id: str, folder: Path) -> dict:
    """
    扫描 folder 下视频/音频/文本文件,生成 source_items 并写入 batch。
    不复制原文件;path 直接指向源,后续 transcribe 阶段从源读。
    返回扫描汇报。
    """
    settings = load_settings()
    folder = Path(folder)
    if not folder.exists() or not folder.is_dir():
        return {"ok": False, "error": f"目录不存在: {folder}"}

    items: list[dict] = []
    seen_hashes: set = set()
    skipped_dup = 0
    skipped_other = 0

    for p in sorted(folder.rglob("*")):
        if not p.is_file():
            continue
        kind = _classify_ext(p.suffix, settings)
        if not kind:
            skipped_other += 1
            continue

        fhash = _file_hash(p)
        if fhash and fhash in seen_hashes:
            skipped_dup += 1
            continue
        seen_hashes.add(fhash)

        meta = _read_companion_metadata(p)
        item_id = f"item_{len(items)+1:04d}_{_slug(p.stem, 24)}"
        items.append({
            "item_id": item_id,
            "platform": "local",
            "kind": kind,  # video | audio | text
            "title": meta.get("title") or p.stem,
            "description": meta.get("description") or "",
            "source_url": meta.get("source_url") or "",
            "cover_url": meta.get("cover_url") or "",
            "published_at": meta.get("published_at") or "",
            "duration_sec": meta.get("duration_sec") or 0,
            "local_video_path": str(p) if kind == "video" else "",
            "local_audio_path": str(p) if kind == "audio" else "",
            "local_text_path":  str(p) if kind == "text"  else "",
            "transcript_path": str(p) if kind == "text" else "",
            "file_hash": fhash,
            "status": "downloaded" if kind in ("video", "audio") else "transcribed",
            "error": "",
            "added_at": _now_iso(),
        })

    save_source_items(batch_id, items)
    m = load_manifest(batch_id)
    m["stats"]["item_count"] = len(items)
    m["stats"]["transcribed"] = sum(1 for it in items if it["status"] == "transcribed")
    save_manifest(batch_id, m)
    set_status(batch_id, "scanned", note=f"扫描 {folder} → {len(items)} 项")

    return {
        "ok": True,
        "batch_id": batch_id,
        "items_added": len(items),
        "skipped_duplicates": skipped_dup,
        "skipped_other_ext": skipped_other,
        "folder": str(folder),
    }


# ─── 单 URL / 平台主页采集(stub + 优雅失败)──────────────
def _collector_unavailable(reason: str) -> dict:
    return {
        "ok": False,
        "error": reason,
        "fallback_message": load_settings().get("policy", {}).get(
            "graceful_failure_message",
            "自动采集失败,请改用本地文件夹导入兜底",
        ),
    }


def collect_single_url(batch_id: str, url: str) -> dict:
    """
    单视频链接采集。
    第一版策略:仅登记 URL 作为 source_item(status=pending),
    不主动下载。如管理员配置了 yt_dlp_exe,可由后续脚本/外部工具下载到 batch_dir/videos。
    任何风控/付费/DRM 内容必须由管理员先手动下载到 raw_input/local 后改用 local_folder 模式。
    """
    if not url.strip():
        return {"ok": False, "error": "URL 为空"}
    items = load_source_items(batch_id)
    item_id = f"item_{len(items)+1:04d}_url"
    items.append({
        "item_id": item_id,
        "platform": "url",
        "kind": "video",
        "title": "",
        "description": "",
        "source_url": url.strip(),
        "cover_url": "",
        "published_at": "",
        "duration_sec": 0,
        "local_video_path": "",
        "local_audio_path": "",
        "local_text_path": "",
        "transcript_path": "",
        "file_hash": "",
        "status": "pending",
        "error": "",
        "added_at": _now_iso(),
        "note": "请使用配置的 downloader 下载到 batch videos 目录,或手动下载后改走 local_folder",
    })
    save_source_items(batch_id, items)
    m = load_manifest(batch_id)
    m["stats"]["item_count"] = len(items)
    save_manifest(batch_id, m)
    set_status(batch_id, "scanned", note=f"登记 URL: {url}")
    return {"ok": True, "batch_id": batch_id, "items_added": 1}


def collect_douyin_homepage(batch_id: str, url: str) -> dict:
    """
    抖音博主主页采集 — 第一版仅登记主页 URL。
    抖音对自动化采集风控严格,本系统不绕过风控。管理员需要使用浏览器/官方工具
    手动下载公开视频到 raw_input/local,再改走 local_folder 模式。
    """
    settings = load_settings()
    if not settings.get("policy", {}).get("douyin_collector_enabled", False):
        # 仅登记 URL,提示 fallback
        items = load_source_items(batch_id)
        items.append({
            "item_id": f"item_{len(items)+1:04d}_douyin_home",
            "platform": "douyin",
            "kind": "homepage",
            "title": "抖音博主主页(待手动采集)",
            "description": "",
            "source_url": url.strip(),
            "cover_url": "",
            "published_at": "",
            "duration_sec": 0,
            "local_video_path": "",
            "local_audio_path": "",
            "local_text_path": "",
            "transcript_path": "",
            "file_hash": "",
            "status": "pending",
            "error": "douyin_collector_disabled",
            "added_at": _now_iso(),
            "note": "抖音风控强,系统不主动采集。请手动下载公开视频到 raw_input/local 后改走本地导入。",
        })
        save_source_items(batch_id, items)
        m = load_manifest(batch_id)
        m["stats"]["item_count"] = len(items)
        save_manifest(batch_id, m)
        set_status(batch_id, "scanned", note=f"登记抖音主页: {url}(未启用采集器)")
        return _collector_unavailable("douyin_collector_disabled")

    # 启用后的真实采集留给将来接入合规第三方工具,这里保留接口
    return _collector_unavailable("douyin_collector not implemented yet")


def collect_bilibili_homepage(batch_id: str, url: str) -> dict:
    """
    B站 UP 主主页采集 — 第一版仅登记主页 URL。
    管理员可在 admin_settings 中填写 bilibili_downloader_exe 路径(如官方工具),
    由外部工具下载公开视频到 batch videos 目录后,本系统再做 ASR 与卡片化。
    本系统不破解 DRM、不处理付费/会员/私密内容。
    """
    settings = load_settings()
    if not settings.get("policy", {}).get("bilibili_collector_enabled", False):
        items = load_source_items(batch_id)
        items.append({
            "item_id": f"item_{len(items)+1:04d}_bili_home",
            "platform": "bilibili",
            "kind": "homepage",
            "title": "B站 UP 主主页(待手动/外部工具采集)",
            "description": "",
            "source_url": url.strip(),
            "cover_url": "",
            "published_at": "",
            "duration_sec": 0,
            "local_video_path": "",
            "local_audio_path": "",
            "local_text_path": "",
            "transcript_path": "",
            "file_hash": "",
            "status": "pending",
            "error": "bilibili_collector_disabled",
            "added_at": _now_iso(),
            "note": "请用合规外部工具下载公开视频到 raw_input/local 后改走本地导入。系统不破解 DRM/付费内容。",
        })
        save_source_items(batch_id, items)
        m = load_manifest(batch_id)
        m["stats"]["item_count"] = len(items)
        save_manifest(batch_id, m)
        set_status(batch_id, "scanned", note=f"登记 B 站主页: {url}(未启用采集器)")
        return _collector_unavailable("bilibili_collector_disabled")

    return _collector_unavailable("bilibili_collector not implemented yet")


# ─── 通用 helpers ─────────────────────────────────────────
def attach_local_file_to_batch(batch_id: str, src_path: Path) -> dict:
    """把 raw_input 或外部文件链接到 batch videos 目录(不复制源,只登记)。"""
    src_path = Path(src_path)
    if not src_path.exists():
        return {"ok": False, "error": f"文件不存在: {src_path}"}
    settings = load_settings()
    kind = _classify_ext(src_path.suffix, settings)
    if not kind:
        return {"ok": False, "error": f"不支持的文件类型: {src_path.suffix}"}

    items = load_source_items(batch_id)
    fhash = _file_hash(src_path)
    if any(it.get("file_hash") == fhash for it in items if fhash):
        return {"ok": False, "error": "重复文件(file_hash 已存在)"}

    item_id = f"item_{len(items)+1:04d}_{_slug(src_path.stem, 24)}"
    items.append({
        "item_id": item_id,
        "platform": "local",
        "kind": kind,
        "title": src_path.stem,
        "description": "",
        "source_url": "",
        "cover_url": "",
        "published_at": "",
        "duration_sec": 0,
        "local_video_path": str(src_path) if kind == "video" else "",
        "local_audio_path": str(src_path) if kind == "audio" else "",
        "local_text_path":  str(src_path) if kind == "text"  else "",
        "transcript_path":  str(src_path) if kind == "text"  else "",
        "file_hash": fhash,
        "status": "downloaded" if kind in ("video", "audio") else "transcribed",
        "error": "",
        "added_at": _now_iso(),
    })
    save_source_items(batch_id, items)
    m = load_manifest(batch_id)
    m["stats"]["item_count"] = len(items)
    save_manifest(batch_id, m)
    return {"ok": True, "item_id": item_id}


def write_ingest_report(batch_id: str, payload: dict) -> Path:
    p = batch_dir(batch_id) / "ingest_report.json"
    payload = dict(payload)
    payload["batch_id"] = batch_id
    payload["written_at"] = _now_iso()
    _write_json(p, payload)
    return p


def write_approval(batch_id: str, decision: str, note: str = "") -> Path:
    """decision: approved | rejected | archive_only"""
    p = batch_dir(batch_id) / "approval.json"
    _write_json(p, {
        "batch_id": batch_id,
        "decision": decision,
        "note": note,
        "decided_at": _now_iso(),
    })
    return p


def backup_official_knowledge(target_dir: Path | None = None) -> dict:
    """合并前备份正式 cards/chunks/profiles。"""
    target_dir = Path(target_dir) if target_dir else (LOGS_ROOT / f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    target_dir.mkdir(parents=True, exist_ok=True)
    src_dir = Path("data/mentor_knowledge")
    copied = []
    for name in ("cards.json", "chunks.json", "mentor_profiles.json", "rejected_cards.json"):
        src = src_dir / name
        if src.exists():
            dst = target_dir / name
            shutil.copy2(src, dst)
            copied.append(name)
    return {"backup_dir": str(target_dir), "files": copied}

