"""
platform_collect_service — P2A 单 URL / B站采集(metadata + 下载 + 空间枚举)

职责:
  - 识别平台(B站 / 通用 URL)
  - P2A-1 collect_url_metadata:yt-dlp 抓单视频 metadata(--dump-single-json --no-download)
                                 → 写 source_items.json(status=metadata_ready)
  - P2A-2 download_video_item:下载单个 metadata_ready 视频到 batch/videos/
                               → 回填 local_video_path / file_hash(sha256),status=downloaded
  - P2A-4 collect_bilibili_space:--flat-playlist 枚举 UP 主空间公开视频候选(浅 metadata)
                                  → 写 collect_report.json
  - P2A-5 import_report_candidates:把选中候选导入 source_items(status=metadata_ready)
  - 任何失败 → 写结构化 error + report.status=failed / source_item.status=failed,绝不崩

明确不做:
  - P2A-3 封面下载已取消:只保留 cover_url,不下载 cover_local_path,无 download_cover task
  - 不抽音频、不触发 ASR、不卡片化、不 approve(交给后续阶段的专用脚本)
  - 不使用 cookies、不绕过平台风控、不破解 DRM、不处理私密/付费/会员内容
  - 不做抖音采集

落盘位置(全部进 staging,沿用现有约定):
  data/mentor_ingest/staging/batches/<batch_id>/source_items.json
  data/mentor_ingest/staging/batches/<batch_id>/meta/<name>.info.json
  data/mentor_ingest/staging/batches/<batch_id>/videos/<stem>.<ext>
  data/mentor_ingest/staging/batches/<batch_id>/collect_report.json
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from services.mentor_ingest_service import (  # noqa: E402
    batch_dir,
    load_settings,
    load_source_items,
    save_source_items,
    load_manifest,
    save_manifest,
    set_status,
    update_item,
)

# metadata 探测超时(秒)。只拉 metadata,不下载,120s 足够。
META_PROBE_TIMEOUT = 120
VERSION_PROBE_TIMEOUT = 20
# 视频下载超时(秒)。单视频 720p,1 小时上限。
DOWNLOAD_TIMEOUT = 3600
DEFAULT_QUALITY = "best[height<=720]/best"
# 空间列表枚举超时(秒)。只拉浅 metadata,5 分钟足够。
SPACE_TIMEOUT = 300
# 空间枚举数量:默认值与硬上限。
DEFAULT_MAX_ITEMS = 20
HARD_MAX_ITEMS = 100

_MID_RE = re.compile(r"space\.bilibili\.com/(\d+)")

# 下载产物中需要排除的 sidecar 后缀(只认媒体文件为正片)
_SIDECAR_EXTS = {
    ".part", ".ytdl", ".json", ".info", ".jpg", ".jpeg", ".png", ".webp",
    ".description", ".srt", ".vtt", ".txt", ".tmp",
}
# 可下载状态(允许直接发起下载)
_DOWNLOADABLE = ("metadata_ready", "pending")

_BV_RE = re.compile(r"(BV[0-9A-Za-z]{10})")
_AV_RE = re.compile(r"av(\d+)", re.IGNORECASE)
_SAFE_RE = re.compile(r"[^A-Za-z0-9一-鿿_\-]")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _err(code: str, message: str) -> dict:
    """结构化 error。成功时 source_item.error 为空字符串,失败时为本对象。"""
    return {"code": code, "message": str(message)[:1000], "at": _now_iso()}


def _safe_name(s: str, max_len: int = 40) -> str:
    s = _SAFE_RE.sub("_", s or "")
    s = re.sub(r"_+", "_", s).strip("_")
    return s[:max_len] or "item"


# ─── 平台识别 ─────────────────────────────────────────────
def detect_platform(url: str) -> str:
    """返回 'bilibili' | 'url'。仅做识别,不访问网络。"""
    u = (url or "").strip().lower()
    if not u:
        return "url"
    if "bilibili.com" in u or "b23.tv" in u:
        return "bilibili"
    if _BV_RE.search(url or "") or _AV_RE.search(u):
        return "bilibili"
    return "url"


def extract_platform_item_id(url: str) -> str:
    """从 URL 抽取 BV 号(优先)或 av 号。抽不到返回空串。"""
    m = _BV_RE.search(url or "")
    if m:
        return m.group(1)
    m = _AV_RE.search(url or "")
    if m:
        return f"av{m.group(1)}"
    return ""


# ─── yt-dlp 探测与调用 ────────────────────────────────────
def _run_capture(cmd: list[str], timeout: int) -> tuple[int, str, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
        return r.returncode, r.stdout or "", r.stderr or ""
    except FileNotFoundError as e:
        return 127, "", f"FileNotFoundError: {e}"
    except subprocess.TimeoutExpired as e:
        return 124, "", f"timeout: {e}"
    except Exception as e:  # noqa: BLE001
        return 1, "", f"{type(e).__name__}: {e}"


def resolve_yt_dlp_base(settings: dict | None = None) -> tuple[list[str] | None, str]:
    """
    解析 yt-dlp 调用前缀,按优先级探测可用性:
      1) admin_settings.tools.yt_dlp_exe(若配置)
      2) PATH 中的 yt-dlp
      3) python -m yt_dlp
    返回 (cmd_prefix, version_or_error)。都不可用时返回 (None, 错误说明)。
    """
    settings = settings or load_settings()
    exe = (settings.get("tools", {}).get("yt_dlp_exe") or "").strip()
    candidates: list[list[str]] = []
    if exe:
        candidates.append([exe])
    candidates.append(["yt-dlp"])
    candidates.append([sys.executable, "-m", "yt_dlp"])

    last_err = ""
    for base in candidates:
        code, out, err = _run_capture(base + ["--version"], VERSION_PROBE_TIMEOUT)
        if code == 0:
            return base, (out or "").strip()
        last_err = err or out or f"returncode={code}"
    return None, f"yt-dlp 不可用(已尝试 {len(candidates)} 种调用方式): {last_err.strip()[:300]}"


def _classify_probe_error(stderr: str) -> str:
    s = (stderr or "").lower()
    if "unsupported url" in s or "no video" in s:
        return "url_unsupported"
    if any(k in s for k in ("private", "members only", "members-only",
                            "paid", "vip", "403", "login", "cookies",
                            "not available", "unavailable", "geo")):
        return "not_accessible"
    return "probe_failed"


def probe_metadata(url: str, settings: dict | None = None) -> dict:
    """
    用 yt-dlp 抓取单视频 metadata(不下载)。
    返回 {"ok": bool, "info": dict|None, "error": dict|None, "yt_dlp_version": str}。
    """
    settings = settings or load_settings()
    base, ver_or_err = resolve_yt_dlp_base(settings)
    if base is None:
        return {"ok": False, "info": None,
                "error": _err("tool_missing", ver_or_err), "yt_dlp_version": ""}

    cmd = base + [
        "--dump-single-json",
        "--no-download",
        "--no-playlist",      # 单视频:即使误传列表/空间链接也只取首个,不展开
        "--no-warnings",
        url,
    ]
    code, out, err = _run_capture(cmd, META_PROBE_TIMEOUT)
    if code != 0 or not out.strip():
        return {"ok": False, "info": None,
                "error": _err(_classify_probe_error(err), err or out or f"returncode={code}"),
                "yt_dlp_version": ver_or_err}
    try:
        info = json.loads(out)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "info": None,
                "error": _err("meta_incomplete", f"yt-dlp JSON 解析失败: {e}"),
                "yt_dlp_version": ver_or_err}
    return {"ok": True, "info": info, "error": None, "yt_dlp_version": ver_or_err}


# ─── metadata 映射 ────────────────────────────────────────
def _fmt_published(info: dict) -> str:
    d = info.get("upload_date") or ""
    if isinstance(d, str) and len(d) == 8 and d.isdigit():
        return f"{d[0:4]}-{d[4:6]}-{d[6:8]}"
    return d or ""


def _map_metadata(info: dict, url: str, platform: str) -> dict:
    bv = extract_platform_item_id(url) or str(info.get("id") or "")
    return {
        "title": info.get("title") or "",
        "description": info.get("description") or "",
        "author_name": (info.get("uploader") or info.get("channel")
                        or info.get("uploader_id") or ""),
        "platform_item_id": bv,
        "source_url": info.get("webpage_url") or url,
        "cover_url": info.get("thumbnail") or "",
        "published_at": _fmt_published(info),
        "duration_sec": int(info.get("duration") or 0) if str(info.get("duration") or "").strip() else 0,
    }


def _new_metadata_item(item_id: str, platform: str, mapped: dict, raw_meta_path: str,
                       status: str, error) -> dict:
    """统一 source_item 结构(metadata 阶段:不写 local_video_path / file_hash;封面不下载)。"""
    return {
        "item_id": item_id,
        "platform": platform,            # bilibili | url
        "kind": "video",
        "title": mapped.get("title", ""),
        "description": mapped.get("description", ""),
        "author_name": mapped.get("author_name", ""),
        "platform_item_id": mapped.get("platform_item_id", ""),
        "source_url": mapped.get("source_url", ""),
        "cover_url": mapped.get("cover_url", ""),
        "cover_local_path": "",          # 封面不下载(P2A-3 取消),仅保留 cover_url
        "published_at": mapped.get("published_at", ""),
        "duration_sec": mapped.get("duration_sec", 0),
        "local_video_path": "",          # P2A-2 才下载视频
        "local_audio_path": "",
        "local_text_path": "",
        "transcript_path": "",
        "transcript_srt_path": "",
        "file_hash": "",                 # 下载后才计算
        "raw_meta_path": raw_meta_path,
        "status": status,                # metadata_ready | failed
        "error": error if error else "",
        "added_at": _now_iso(),
    }


# ─── 主入口 ───────────────────────────────────────────────
def collect_url_metadata(batch_id: str, url: str) -> dict:
    """
    单 URL metadata 采集(P2A-1)。
    成功:向 batch 追加 1 条 status=metadata_ready 的 source_item,返回 {"ok": True, ...}。
    失败:向 batch 追加 1 条 status=failed + 结构化 error 的 source_item,返回 {"ok": False, ...}。
    任何分支都不抛异常、不下载、不发 ASR。
    """
    url = (url or "").strip()
    if not url:
        return {"ok": False, "error": "URL 为空", "item_id": ""}

    settings = load_settings()
    platform = detect_platform(url)
    items = load_source_items(batch_id)
    seq = len(items) + 1
    bv = extract_platform_item_id(url)
    item_id = f"item_{seq:04d}_{_safe_name(bv or platform)}"

    result = probe_metadata(url, settings)

    # 原始 metadata 落盘(成功时)
    raw_meta_path = ""
    if result.get("ok") and result.get("info") is not None:
        meta_dir = batch_dir(batch_id) / "meta"
        meta_dir.mkdir(parents=True, exist_ok=True)
        meta_name = _safe_name(bv or item_id)
        meta_file = meta_dir / f"{meta_name}.info.json"
        try:
            meta_file.write_text(
                json.dumps(result["info"], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            raw_meta_path = str(meta_file)
        except Exception:  # noqa: BLE001
            raw_meta_path = ""

    if result.get("ok"):
        mapped = _map_metadata(result["info"], url, platform)
        item = _new_metadata_item(item_id, platform, mapped, raw_meta_path,
                                  status="metadata_ready", error="")
    else:
        # 失败也登记一条,保留 URL 与 error,方便管理员重试或改本地导入
        mapped = {"source_url": url}
        item = _new_metadata_item(item_id, platform, mapped, raw_meta_path,
                                  status="failed", error=result.get("error"))

    items.append(item)
    save_source_items(batch_id, items)

    # 更新 manifest 计数(不改 batch 主状态机的语义,仅补 scanned 便于 UI 展示)
    m = load_manifest(batch_id)
    if m:
        m.setdefault("stats", {})
        m["stats"]["item_count"] = len(items)
        save_manifest(batch_id, m)
        if m.get("status") in ("", None):
            set_status(batch_id, "scanned", note=f"collect_url_metadata: {url}")

    ok = bool(result.get("ok"))
    out = {
        "ok": ok,
        "batch_id": batch_id,
        "item_id": item_id,
        "platform": platform,
        "status": item["status"],
        "yt_dlp_version": result.get("yt_dlp_version", ""),
    }
    if not ok:
        out["error"] = result.get("error")
    else:
        out["title"] = item["title"]
        out["author_name"] = item["author_name"]
        out["duration_sec"] = item["duration_sec"]
        out["platform_item_id"] = item["platform_item_id"]
    return out


# ─── 视频下载(P2A-2)───────────────────────────────────────
def _sha256_file(path: Path, chunk_size: int = 1 << 20) -> str:
    """整文件 sha256(P2A-2 要求精确去重,与 ingest 的快速 sha1 不同)。"""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(chunk_size), b""):
            h.update(block)
    return f"sha256:{h.hexdigest()}"


def _classify_download_error(stderr: str) -> str:
    s = (stderr or "").lower()
    if any(k in s for k in ("private", "members only", "members-only", "paid",
                            "vip", "403", "login required", "cookies",
                            "not available", "unavailable", "geo", "drm")):
        return "not_accessible"
    return "download_failed"


def _find_downloaded_file(videos_dir: Path, stem: str) -> Path | None:
    """在 videos_dir 找以 stem 开头、非 sidecar 的最大媒体文件。"""
    candidates = []
    for p in videos_dir.glob(f"{stem}.*"):
        if not p.is_file():
            continue
        if p.suffix.lower() in _SIDECAR_EXTS:
            continue
        candidates.append(p)
    if not candidates:
        return None
    return max(candidates, key=lambda x: x.stat().st_size)


def download_video_item(batch_id: str, item_id: str, quality: str = DEFAULT_QUALITY,
                        force: bool = False) -> dict:
    """
    下载单个 metadata_ready source_item 的视频到 batch/videos/。
    成功:回填 local_video_path / file_hash,status=downloaded,error 清空。
    失败:status=failed + 结构化 error;duplicate:status=skipped + error.code=duplicate。
    任何分支都不抛异常、不下载封面(P2A-3 取消)、不触发 ASR。
    """
    items = load_source_items(batch_id)
    item = next((it for it in items if it.get("item_id") == item_id), None)
    if item is None:
        return {"ok": False, "item_id": item_id,
                "error": _err("item_not_found", f"source_item 不存在: {item_id}")}

    status = item.get("status", "")
    src_url = (item.get("source_url") or "").strip()

    # 已下载:默认跳过,force 才重下
    existing_path = item.get("local_video_path") or ""
    if status == "downloaded" and existing_path and Path(existing_path).exists() and not force:
        return {"ok": True, "item_id": item_id, "status": "downloaded",
                "skipped": True, "reason": "already_downloaded",
                "local_video_path": existing_path}

    # 状态闸门:已进入下游处理的不覆盖,除非 force
    if status in ("transcribed", "carded", "approved", "audio_extracted") and not force:
        return {"ok": False, "item_id": item_id,
                "error": _err("already_downloaded",
                              f"item 已处于下游状态({status}),需 force=true 才重下")}

    warning = ""
    if status not in _DOWNLOADABLE and status != "downloaded" and status != "failed":
        # 兜底:未知状态,记 warning 但仍尝试(若有 url)
        warning = f"item 状态为 {status},非常规可下载状态,仍尝试下载"
    if status == "failed":
        warning = "item 此前 metadata 抓取失败,尝试用 source_url 直接下载"

    if not src_url:
        update_item(batch_id, item_id, status="failed",
                    error=_err("source_url_missing", "source_item 缺少 source_url,无法下载"))
        return {"ok": False, "item_id": item_id,
                "error": _err("source_url_missing", "缺少 source_url")}

    settings = load_settings()
    base, ver_or_err = resolve_yt_dlp_base(settings)
    if base is None:
        update_item(batch_id, item_id, status="failed",
                    error=_err("tool_missing", ver_or_err))
        return {"ok": False, "item_id": item_id,
                "error": _err("tool_missing", ver_or_err)}

    videos_dir = batch_dir(batch_id) / "videos"
    videos_dir.mkdir(parents=True, exist_ok=True)
    stem = _safe_name(item.get("platform_item_id") or item_id)
    out_tmpl = str(videos_dir / f"{stem}.%(ext)s")

    cmd = base + [
        "-f", quality or DEFAULT_QUALITY,
        "--no-playlist",
        "--no-warnings",
        "-o", out_tmpl,
        src_url,
    ]
    code, out, err = _run_capture(cmd, DOWNLOAD_TIMEOUT)
    log_text = (out or "") + ("\n" + err if err else "")

    if code != 0:
        update_item(batch_id, item_id, status="failed",
                    error=_err(_classify_download_error(err), err or out or f"returncode={code}"))
        return {"ok": False, "item_id": item_id, "log": log_text[-2000:],
                "error": _err(_classify_download_error(err), err or out or f"returncode={code}")}

    video_file = _find_downloaded_file(videos_dir, stem)
    if video_file is None:
        update_item(batch_id, item_id, status="failed",
                    error=_err("output_missing", "yt-dlp 返回 0 但未找到输出视频文件"))
        return {"ok": False, "item_id": item_id, "log": log_text[-2000:],
                "error": _err("output_missing", "未找到输出文件")}

    fhash = _sha256_file(video_file)

    # 去重:同 batch 其它 item 已有相同 file_hash → 标 skipped/duplicate,不删文件
    dup = next((it for it in items
                if it.get("item_id") != item_id and it.get("file_hash") == fhash), None)
    if dup is not None:
        update_item(batch_id, item_id, status="skipped",
                    local_video_path=str(video_file), file_hash=fhash,
                    error=_err("duplicate", f"与 {dup.get('item_id')} 文件 hash 相同"))
        return {"ok": True, "item_id": item_id, "status": "skipped",
                "duplicate_of": dup.get("item_id"), "file_hash": fhash,
                "local_video_path": str(video_file), "log": log_text[-2000:]}

    update_item(batch_id, item_id, status="downloaded",
                local_video_path=str(video_file), file_hash=fhash, error="")

    res = {"ok": True, "item_id": item_id, "status": "downloaded",
           "local_video_path": str(video_file), "file_hash": fhash,
           "yt_dlp_version": ver_or_err, "log": log_text[-2000:]}
    if warning:
        res["warning"] = warning
    return res


# ─── B站空间/主页候选枚举(P2A-4)──────────────────────────
def extract_author_id(url: str) -> str:
    """从 space.bilibili.com/<mid> 抽取 mid。抽不到返回空串。"""
    m = _MID_RE.search(url or "")
    return m.group(1) if m else ""


def _report_path(batch_id: str) -> Path:
    return batch_dir(batch_id) / "collect_report.json"


def load_collect_report(batch_id: str) -> dict:
    p = _report_path(batch_id)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _save_collect_report(batch_id: str, report: dict) -> None:
    _report_path(batch_id).write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def _classify_space_error(stderr: str) -> str:
    s = (stderr or "").lower()
    if "unsupported url" in s:
        return "url_unsupported"
    if any(k in s for k in ("login", "cookies", "account")):
        return "auth_required"
    if any(k in s for k in ("private", "403", "not available", "unavailable", "geo")):
        return "not_accessible"
    return "space_collect_failed"


def _parse_flat_entries(stdout: str) -> list[dict]:
    """yt-dlp --flat-playlist 可能输出单个 JSON(带 entries)或逐行 JSON,两者都兼容。"""
    out = stdout.strip()
    if not out:
        return []
    entries: list[dict] = []
    # 先尝试整体解析(--dump-single-json 形态)
    try:
        obj = json.loads(out)
        if isinstance(obj, dict) and isinstance(obj.get("entries"), list):
            return [e for e in obj["entries"] if isinstance(e, dict)]
        if isinstance(obj, dict):
            return [obj]
        if isinstance(obj, list):
            return [e for e in obj if isinstance(e, dict)]
    except Exception:  # noqa: BLE001
        pass
    # 回退:逐行 JSON(--dump-json 形态)
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
            if isinstance(e, dict):
                entries.append(e)
        except Exception:  # noqa: BLE001
            continue
    return entries


def _entry_to_candidate(idx: int, e: dict) -> dict:
    bv = str(e.get("id") or "")
    m = _BV_RE.search(bv) or _BV_RE.search(str(e.get("url") or ""))
    if m:
        bv = m.group(1)
    src = e.get("webpage_url") or e.get("url") or ""
    if src and not str(src).startswith("http") and bv:
        src = f"https://www.bilibili.com/video/{bv}"
    return {
        "candidate_id": f"cand_{idx:04d}_{_safe_name(bv or str(idx))}",
        "platform": "bilibili",
        "kind": "video",
        "title": e.get("title") or "",
        "description": e.get("description") or "",
        "author_name": (e.get("uploader") or e.get("channel") or ""),
        "platform_item_id": bv,
        "source_url": src,
        "cover_url": e.get("thumbnail") or "",
        "published_at": _fmt_published(e),
        "duration_sec": int(e.get("duration") or 0) if str(e.get("duration") or "").strip() else 0,
        "status": "candidate",
        "selected": False,
        "imported_item_id": "",
        "error": "",
    }


def collect_bilibili_space(batch_id: str, space_url: str,
                           max_items: int | None = None) -> dict:
    """
    枚举 B站 UP 主空间/主页公开视频候选列表(浅 metadata),写 collect_report.json。
    不下载视频、不下载封面、不逐条深抓。失败写 report.status=failed + error,不崩。
    """
    space_url = (space_url or "").strip()
    settings = load_settings()
    if max_items is None:
        max_items = int(settings.get("ingest", {}).get("bilibili_max_items", DEFAULT_MAX_ITEMS))
    max_items = max(1, min(int(max_items), HARD_MAX_ITEMS))
    author_id = extract_author_id(space_url)

    def _fail(code: str, msg: str) -> dict:
        report = {
            "batch_id": batch_id, "platform": "bilibili", "source_url": space_url,
            "author_id": author_id, "author_name": "", "max_items": max_items,
            "total_seen": 0, "truncated": False, "generated_at": _now_iso(),
            "status": "failed", "error": _err(code, msg), "items": [],
        }
        _save_collect_report(batch_id, report)
        return {"ok": False, "batch_id": batch_id, "error": _err(code, msg),
                "report_path": str(_report_path(batch_id))}

    if not space_url:
        return _fail("url_unsupported", "space_url 为空")

    base, ver_or_err = resolve_yt_dlp_base(settings)
    if base is None:
        return _fail("tool_missing", ver_or_err)

    # --flat-playlist 浅枚举,限制 1..max_items,不下载
    cmd = base + [
        "--flat-playlist",
        "--dump-json",
        "--no-warnings",
        "-I", f"1:{max_items}",
        space_url,
    ]
    code, out, err = _run_capture(cmd, SPACE_TIMEOUT)
    if code != 0 and not out.strip():
        return _fail(_classify_space_error(err), err or out or f"returncode={code}")

    entries = _parse_flat_entries(out)
    total_seen = len(entries)
    if total_seen == 0:
        return _fail("no_items_found", err or "未枚举到任何视频候选")

    truncated = total_seen > max_items
    entries = entries[:max_items]
    candidates = [_entry_to_candidate(i + 1, e) for i, e in enumerate(entries)]
    author_name = next((c["author_name"] for c in candidates if c["author_name"]), "")

    report = {
        "batch_id": batch_id, "platform": "bilibili", "source_url": space_url,
        "author_id": author_id, "author_name": author_name,
        "max_items": max_items, "total_seen": total_seen, "truncated": truncated,
        "generated_at": _now_iso(), "status": "success", "error": "",
        "yt_dlp_version": ver_or_err, "items": candidates,
    }
    _save_collect_report(batch_id, report)
    return {"ok": True, "batch_id": batch_id, "candidate_count": len(candidates),
            "total_seen": total_seen, "truncated": truncated,
            "report_path": str(_report_path(batch_id))}


# ─── 候选导入 source_items(P2A-5)─────────────────────────
def import_report_candidates(batch_id: str, candidate_ids: list[str]) -> dict:
    """
    把 collect_report 中选中的 candidates 导入 source_items(status=metadata_ready)。
    去重:已有相同 platform_item_id 或 source_url 的 source_item 则跳过/复用,不重复添加。
    回写 report.items[].status=imported + imported_item_id。仅本地 JSON 操作,不下载。
    """
    report = load_collect_report(batch_id)
    if not report or not isinstance(report.get("items"), list):
        return {"ok": False, "error": "collect_report.json 不存在或无 items",
                "imported_count": 0, "skipped_duplicate_count": 0, "item_ids": []}

    wanted = set(candidate_ids or [])
    items = load_source_items(batch_id)

    def _find_existing(cand: dict) -> dict | None:
        bv = cand.get("platform_item_id") or ""
        url = cand.get("source_url") or ""
        for it in items:
            if bv and it.get("platform_item_id") == bv:
                return it
            if url and it.get("source_url") == url:
                return it
        return None

    imported_ids: list[str] = []
    skipped = 0
    changed_report = False
    seq = len(items)

    for cand in report["items"]:
        if cand.get("candidate_id") not in wanted:
            continue
        existing = _find_existing(cand)
        if existing is not None:
            skipped += 1
            if cand.get("status") != "imported" or not cand.get("imported_item_id"):
                cand["status"] = "imported"
                cand["imported_item_id"] = existing.get("item_id", "")
                changed_report = True
            continue

        seq += 1
        bv = cand.get("platform_item_id") or ""
        item_id = f"item_{seq:04d}_{_safe_name(bv or 'bili')}"
        items.append({
            "item_id": item_id,
            "platform": cand.get("platform", "bilibili"),
            "kind": "video",
            "title": cand.get("title", ""),
            "description": cand.get("description", ""),
            "author_name": cand.get("author_name", ""),
            "platform_item_id": bv,
            "source_url": cand.get("source_url", ""),
            "cover_url": cand.get("cover_url", ""),
            "cover_local_path": "",
            "published_at": cand.get("published_at", ""),
            "duration_sec": cand.get("duration_sec", 0),
            "local_video_path": "",
            "local_audio_path": "",
            "local_text_path": "",
            "transcript_path": "",
            "transcript_srt_path": "",
            "file_hash": "",
            "raw_meta_path": "",
            "status": "metadata_ready",
            "error": "",
            "added_at": _now_iso(),
        })
        imported_ids.append(item_id)
        cand["status"] = "imported"
        cand["imported_item_id"] = item_id
        changed_report = True

    if imported_ids:
        save_source_items(batch_id, items)
        m = load_manifest(batch_id)
        if m:
            m.setdefault("stats", {})
            m["stats"]["item_count"] = len(items)
            save_manifest(batch_id, m)
    if changed_report:
        _save_collect_report(batch_id, report)

    return {"ok": True, "batch_id": batch_id,
            "imported_count": len(imported_ids),
            "skipped_duplicate_count": skipped,
            "item_ids": imported_ids}


