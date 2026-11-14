"""
platform_download_service — P4D 平台素材主页下载服务

职责：
  - 识别主页 URL 所属平台（bilibili / youtube / douyin / unknown）
  - 枚举主页公开视频候选列表（不下载）
  - 批量下载候选视频到 auto_ingest_root/<账号名__platform>/
  - 收集失败记录，支持单条重试

明确不做：
  - 不触发转写 / 卡片化 / approve
  - 不写 data/mentor_knowledge 正式知识库
  - 不使用 cookies、不绕过平台风控、不处理私密/付费/DRM 内容
  - 不改 platform_collect_service（P2A 落点不同，独立维护）

落盘位置（全部进 auto_ingest_root）：
  <auto_ingest_root>/<author_name>__<platform>/<stem>.<ext>
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from services.mentor_ingest_service import load_settings

REPORT_FILENAME = "download_report.json"

# ─── 常量 ────────────────────────────────────────────────
# 扫描上限：B站走公开 API，可扫较大数量；YT/抖音仍用 yt-dlp，保守限制
DEFAULT_SCAN_BILI   = 300
HARD_SCAN_BILI      = 1000
DEFAULT_SCAN_OTHER  = 30     # YouTube / 抖音默认
HARD_SCAN_OTHER     = 100    # YouTube / 抖音上限

# 下载格式：低画质视频 + 完整音频优先；ffmpeg 不可用时回落 worst 单流
DEFAULT_QUALITY = "bestvideo[height<=360]+bestaudio/best[height<=360]/worst"
DEFAULT_QUALITY_NO_MERGE = "best[height<=360]/worst"  # ffmpeg 不可用时的备用格式

DEFAULT_RATE_LIMIT = "1M"
SCAN_TIMEOUT = 600           # 枚举超时（秒，大分页需要更长）
DOWNLOAD_TIMEOUT = 3600      # 单条下载超时（秒）
VERSION_PROBE_TIMEOUT = 20

# B站公开 API 分页
_BILI_API_PAGE_SIZE = 30
_BILI_API_TIMEOUT = 15       # 单次 API 请求超时
# max_pages 在运行时由 _calc_bili_max_pages(max_items) 计算，不再固定
_BILI_API_RETRY = 2          # 单页失败后重试次数

# 付费 / 不可访问视频识别关键词（yt-dlp stderr / stdout）
_RESTRICTED_PATTERNS = re.compile(
    r"(付费|充电专属|大会员|premium|members.only|login.required"
    r"|private|not.available|unavailable|geo.?restrict"
    r"|403|401|payment.required|paid.content"
    r"|access.denied|copyright|this.video.is.not)",
    re.IGNORECASE,
)

_SAFE_RE = re.compile(r"[^A-Za-z0-9一-鿿_\-]")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _safe_name(s: str, max_len: int = 40) -> str:
    s = _SAFE_RE.sub("_", s or "")
    s = re.sub(r"_+", "_", s).strip("_")
    return s[:max_len] or "item"


def _err(code: str, message: str) -> dict:
    return {"code": code, "message": str(message)[:1000], "at": _now_iso()}


def _run_capture(cmd: list[str], timeout: int) -> tuple[int, str, str]:
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
        )
        return r.returncode, r.stdout or "", r.stderr or ""
    except FileNotFoundError as e:
        return 127, "", f"FileNotFoundError: {e}"
    except subprocess.TimeoutExpired as e:
        return 124, "", f"timeout: {e}"
    except Exception as e:  # noqa: BLE001
        return 1, "", f"{type(e).__name__}: {e}"


# ─── yt-dlp 可用性探测 ────────────────────────────────────
def resolve_yt_dlp_base(settings: dict | None = None) -> tuple[list[str] | None, str]:
    """
    按优先级探测 yt-dlp 可用性：
      1) admin_settings.tools.yt_dlp_exe
      2) PATH 中的 yt-dlp
      3) python -m yt_dlp
    返回 (cmd_prefix, version_or_error)。
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
    return None, f"yt-dlp 不可用（已尝试 {len(candidates)} 种方式）: {last_err.strip()[:300]}"


# ─── 平台识别 ────────────────────────────────────────────
def detect_homepage_platform(url: str) -> str:
    """
    识别主页 URL 所属平台。
    返回 'bilibili' | 'youtube' | 'douyin' | 'unknown'。
    """
    u = (url or "").strip().lower()
    if not u:
        return "unknown"
    if "bilibili.com/space" in u or "space.bilibili.com" in u:
        return "bilibili"
    # 兼容普通 BV/av 单视频被误填的情况，仍归 bilibili
    if "bilibili.com" in u or "b23.tv" in u:
        return "bilibili"
    if (
        "youtube.com/@" in u
        or "youtube.com/channel/" in u
        or "youtube.com/user/" in u
        or "youtube.com/c/" in u
        or "youtu.be" in u
    ):
        return "youtube"
    if "douyin.com/user/" in u or "v.douyin.com" in u or "douyin.com/@" in u:
        return "douyin"
    return "unknown"


def is_single_video_url(url: str) -> bool:
    """
    判断 URL 是否像单视频链接而非主页/频道链接。
    用于在扫描结果为空时给出友好提示。
    """
    u = (url or "").strip().lower()
    # B站单视频
    if re.search(r"bilibili\.com/video/", u):
        return True
    if re.search(r"b23\.tv/", u):
        return True
    if re.search(r"bilibili\.com/(av|bv)", u):
        return True
    # YouTube 单视频
    if "youtube.com/watch" in u:
        return True
    if re.search(r"youtu\.be/[a-z0-9_\-]{5,}", u):
        return True
    # 抖音单视频/短链
    if re.search(r"douyin\.com/video/", u):
        return True
    if re.search(r"v\.douyin\.com/[a-z0-9]+", u):
        return True
    return False


# ─── 枚举结果解析 ────────────────────────────────────────
def _parse_flat_entries(stdout: str) -> list[dict]:
    """兼容 --dump-single-json（带 entries）和逐行 --dump-json 两种格式。"""
    out = stdout.strip()
    if not out:
        return []
    # 先尝试整体解析
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
    # 回退：逐行 JSON
    entries: list[dict] = []
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


def _fmt_published(info: dict) -> str:
    d = info.get("upload_date") or ""
    if isinstance(d, str) and len(d) == 8 and d.isdigit():
        return f"{d[0:4]}-{d[4:6]}-{d[6:8]}"
    return d or ""


def _entry_to_candidate(idx: int, e: dict, platform: str) -> dict:
    vid = str(e.get("id") or "")
    src = e.get("webpage_url") or e.get("url") or ""
    # B站：补全短链
    if platform == "bilibili" and src and not str(src).startswith("http") and vid:
        src = f"https://www.bilibili.com/video/{vid}"
    return {
        "candidate_id": f"cand_{idx:04d}_{_safe_name(vid or str(idx))}",
        "platform": platform,
        "title": e.get("title") or "",
        "author_name": e.get("uploader") or e.get("channel") or e.get("uploader_id") or "",
        "platform_item_id": vid,
        "source_url": src,
        "cover_url": e.get("thumbnail") or "",
        "published_at": _fmt_published(e),
        "duration_sec": int(e.get("duration") or 0) if str(e.get("duration") or "").strip() else 0,
        "status": "candidate",
    }


# ─── B站 UID 提取 ─────────────────────────────────────────
def _calc_bili_max_pages(max_items: int) -> int:
    """根据扫描上限动态计算最大翻页数，留 2 页余量，硬上限 50。"""
    import math
    pages = math.ceil(max_items / _BILI_API_PAGE_SIZE) + 2
    return min(pages, 50)


def _extract_bili_mid(url: str) -> str | None:
    """
    从 B站空间 URL 提取 UID（mid）。
    支持：
      https://space.bilibili.com/12345
      https://space.bilibili.com/12345/
      https://space.bilibili.com/12345/upload/video
      https://space.bilibili.com/12345/video
      https://www.bilibili.com/space/12345
    """
    m = re.search(r"(?:space\.bilibili\.com|bilibili\.com/space)/(\d+)", url or "")
    return m.group(1) if m else None


def _bili_api_fetch_page(mid: str, pn: int, ps: int) -> tuple[list[dict], int, str]:
    """
    调用 B站公开视频列表 API 获取第 pn 页。
    返回 (vlist, total_count, error_msg)。
    vlist 为空 + error_msg 为空 表示本页无视频（已到末尾）。
    """
    api_url = (
        f"https://api.bilibili.com/x/space/arc/search"
        f"?mid={mid}&ps={ps}&pn={pn}&order=pubdate&jsonp=jsonp"
    )
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Referer": f"https://space.bilibili.com/{mid}/video",
    }
    req = urllib.request.Request(api_url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=_BILI_API_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return [], 0, f"HTTP {e.code}: {e.reason}"
    except urllib.error.URLError as e:
        return [], 0, f"URLError: {e.reason}"
    except Exception as e:  # noqa: BLE001
        return [], 0, f"{type(e).__name__}: {e}"

    try:
        obj = json.loads(raw)
    except Exception:  # noqa: BLE001
        return [], 0, "JSON 解析失败"

    code_val = obj.get("code", -1)
    if code_val != 0:
        msg = obj.get("message") or obj.get("msg") or f"code={code_val}"
        return [], 0, f"API 返回错误：{msg}"

    data = obj.get("data", {})
    arc_search = data if isinstance(data, dict) else {}

    # API 结构：data.list.vlist 或 data.vlist
    vlist_raw = (
        arc_search.get("list", {}).get("vlist")
        or arc_search.get("vlist")
        or []
    )
    total = int(
        arc_search.get("page", {}).get("count", 0)
        or arc_search.get("count", 0)
        or 0
    )
    if not isinstance(vlist_raw, list):
        return [], total, ""
    return vlist_raw, total, ""


def _bili_vlist_to_candidate(idx: int, v: dict, uploader: str) -> dict:
    """将 B站 API vlist 条目转为统一 candidate 结构。"""
    bvid = str(v.get("bvid") or v.get("aid") or "")
    src = f"https://www.bilibili.com/video/{bvid}" if bvid else ""
    # 兜底顺序：title → name → dynamic → description，均取原始值不清洗
    title = (
        v.get("title")
        or v.get("name")
        or v.get("dynamic")
        or v.get("description")
        or ""
    )
    # 去掉两端空白；如果清洗后为空，保留空字符串（UI 层再补"无标题"）
    title = title.strip()
    author = v.get("author") or v.get("uname") or v.get("name") or uploader or ""
    # 发布时间：API 返回 Unix timestamp
    ts = v.get("created") or v.get("pubdate") or 0
    published_at = ""
    if ts:
        try:
            published_at = datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d")
        except Exception:  # noqa: BLE001
            pass
    return {
        "candidate_id": f"cand_{idx:04d}_{_safe_name(bvid or str(idx))}",
        "platform": "bilibili",
        "title": title,
        "author_name": author,
        "platform_item_id": bvid,
        "source_url": src,
        "cover_url": v.get("pic") or "",
        "published_at": published_at,
        "duration_sec": 0,
        "status": "candidate",
    }


def _scan_bilibili_via_api(
    url: str,
    max_items: int,
) -> dict:
    """
    通过 B站公开 API 分页枚举 UP 主视频列表。
    仅调用公开接口，不登录，不拿付费/私密内容正文。
    返回与 scan_bilibili_homepage 相同的结构，并附带：
      paged_scan=True, pages_fetched=N, warnings=[...]
    """
    mid = _extract_bili_mid(url)
    if not mid:
        return {
            "ok": False, "platform": "bilibili",
            "error": _err("url_parse_failed", "无法从 URL 提取 B站 UID，请使用空间主页链接"),
            "candidates": [], "author_name": "", "experimental": False,
            "paged_scan": True,
        }

    seen_bvids: set[str] = set()
    candidates: list[dict] = []
    author_name = ""
    total_count = 0
    warnings: list[str] = []
    consecutive_errors = 0
    _max_pages = _calc_bili_max_pages(max_items)

    for pn in range(1, _max_pages + 1):
        if len(candidates) >= max_items:
            break

        # 礼貌延迟（第一页不延迟）
        if pn > 1:
            time.sleep(0.5)

        attempt_vlist: list[dict] = []
        attempt_err = ""
        for _try in range(_BILI_API_RETRY + 1):
            vlist, total, err = _bili_api_fetch_page(mid, pn, _BILI_API_PAGE_SIZE)
            if not err:
                attempt_vlist = vlist
                if total:
                    total_count = total
                break
            attempt_err = err
            if _try < _BILI_API_RETRY:
                time.sleep(1)

        if attempt_err:
            consecutive_errors += 1
            warnings.append(f"第 {pn} 页请求失败（{attempt_err}），已跳过")
            if consecutive_errors >= 2:
                warnings.append("连续 2 页请求失败，停止翻页")
                break
            continue

        consecutive_errors = 0

        if not attempt_vlist:
            # 本页无视频，说明已到末尾
            break

        for v in attempt_vlist:
            if len(candidates) >= max_items:
                break
            bvid = str(v.get("bvid") or v.get("aid") or "")
            dedup_key = bvid or v.get("title", "")
            if dedup_key and dedup_key in seen_bvids:
                continue
            if dedup_key:
                seen_bvids.add(dedup_key)
            idx = len(candidates) + 1
            cand = _bili_vlist_to_candidate(idx, v, author_name)
            if not author_name and cand["author_name"]:
                author_name = cand["author_name"]
            candidates.append(cand)

    if not candidates:
        warn_detail = "；".join(warnings) if warnings else "API 返回空列表，可能是 mid 无视频或接口结构变化"
        return {
            "ok": False, "platform": "bilibili",
            "error": _err("no_items_found",
                          f"mid={mid}，未枚举到任何视频。{warn_detail}"),
            "candidates": [], "author_name": "", "experimental": False,
            "paged_scan": True,
            "bili_mid": mid,
        }

    # 账号名由 UI 层手动填写兜底，此处不再尝试额外接口
    if not author_name:
        warnings.append(f"账号名未自动获取（mid={mid}），请在下载前手动填写归档账号名")

    return {
        "ok": True, "platform": "bilibili",
        "author_name": author_name,
        "total_seen": total_count or len(candidates),
        "truncated": total_count > max_items if total_count else len(candidates) >= max_items,
        "candidates": candidates,
        "yt_dlp_version": "",
        "experimental": False,
        "error": "",
        "paged_scan": True,
        "pages_fetched": (len(candidates) + _BILI_API_PAGE_SIZE - 1) // _BILI_API_PAGE_SIZE,
        "warnings": warnings,
    }


# ─── 三平台扫描 ───────────────────────────────────────────
def scan_bilibili_homepage(
    url: str,
    max_items: int = DEFAULT_SCAN_BILI,
    settings: dict | None = None,
) -> dict:
    """
    枚举 B站 UP 主空间公开视频候选（不下载）。
    策略：
      1) 优先尝试 B站公开 API 分页（支持多页，去重，优雅停止）
      2) API 不可用（非空间 URL / 网络问题）时回落到 yt-dlp --flat-playlist
    """
    settings = settings or load_settings()

    # 策略 1：B站公开 API 分页（空间 URL 才走此路径）
    mid = _extract_bili_mid(url)
    api_result = None
    if mid:
        api_result = _scan_bilibili_via_api(url, max_items)
        if api_result.get("ok"):
            return api_result
        # API 完全失败 → 尝试 yt-dlp 兜底

    # 策略 2：yt-dlp --flat-playlist 兜底（或非空间 URL）
    base, ver_or_err = resolve_yt_dlp_base(settings)
    if base is None:
        if mid:
            # 透传 API 失败的具体原因
            api_err = api_result.get("error", {}) if api_result else {}
            api_msg = (
                api_err.get("message", "未知错误")
                if isinstance(api_err, dict) else str(api_err)
            )
            ytdlp_hint = (
                "yt-dlp 未安装，无法使用兜底扫描。\n"
                "安装命令：python -m pip install -U yt-dlp"
            )
            detail = (
                f"已识别 mid={mid}，但 B站 API 扫描失败：{api_msg}。"
                f"\n{ytdlp_hint}"
            )
            return {
                "ok": False, "platform": "bilibili",
                "error": _err("scan_failed", detail),
                "candidates": [], "author_name": "", "experimental": False,
                "bili_mid": mid,
            }
        return {"ok": False, "platform": "bilibili", "error": _err("tool_missing", ver_or_err),
                "candidates": [], "author_name": "", "experimental": False}

    cmd = base + [
        "--flat-playlist", "--dump-json", "--no-warnings",
        "-I", f"1:{max_items}",
        url,
    ]
    code, out, err = _run_capture(cmd, SCAN_TIMEOUT)
    if code != 0 and not out.strip():
        return {"ok": False, "platform": "bilibili",
                "error": _err("scan_failed", err or out or f"returncode={code}"),
                "candidates": [], "author_name": "", "experimental": False}

    entries = _parse_flat_entries(out)
    if not entries:
        return {"ok": False, "platform": "bilibili",
                "error": _err("no_items_found", err or "未枚举到任何视频"),
                "candidates": [], "author_name": "", "experimental": False}

    candidates = [_entry_to_candidate(i + 1, e, "bilibili") for i, e in enumerate(entries[:max_items])]
    author_name = next((c["author_name"] for c in candidates if c["author_name"]), "")
    return {
        "ok": True, "platform": "bilibili",
        "author_name": author_name,
        "total_seen": len(entries),
        "truncated": len(entries) > max_items,
        "candidates": candidates,
        "yt_dlp_version": ver_or_err,
        "experimental": False,
        "error": "",
        "paged_scan": False,
    }


def scan_youtube_homepage(
    url: str,
    max_items: int = DEFAULT_SCAN_OTHER,
    settings: dict | None = None,
) -> dict:
    """枚举 YouTube 频道/播放列表公开视频候选（不下载）。"""
    settings = settings or load_settings()
    base, ver_or_err = resolve_yt_dlp_base(settings)
    if base is None:
        return {"ok": False, "platform": "youtube", "error": _err("tool_missing", ver_or_err),
                "candidates": [], "author_name": "", "experimental": False}

    # YouTube 频道用 --dump-single-json 拿整体 playlist（含 entries 数组）
    cmd = base + [
        "--flat-playlist", "--dump-single-json", "--no-warnings",
        "-I", f"1:{max_items}",
        url,
    ]
    code, out, err = _run_capture(cmd, SCAN_TIMEOUT)
    if code != 0 and not out.strip():
        return {"ok": False, "platform": "youtube",
                "error": _err("scan_failed", err or out or f"returncode={code}"),
                "candidates": [], "author_name": "", "experimental": False}

    entries = _parse_flat_entries(out)
    if not entries:
        return {"ok": False, "platform": "youtube",
                "error": _err("no_items_found", err or "未枚举到任何视频"),
                "candidates": [], "author_name": "", "experimental": False}

    candidates = [_entry_to_candidate(i + 1, e, "youtube") for i, e in enumerate(entries[:max_items])]
    author_name = next((c["author_name"] for c in candidates if c["author_name"]), "")
    return {
        "ok": True, "platform": "youtube",
        "author_name": author_name,
        "total_seen": len(entries),
        "truncated": len(entries) > max_items,
        "candidates": candidates,
        "yt_dlp_version": ver_or_err,
        "experimental": False,
        "error": "",
    }


def scan_douyin_homepage(
    url: str,
    max_items: int = DEFAULT_SCAN_OTHER,
    settings: dict | None = None,
) -> dict:
    """
    枚举抖音主页公开视频候选（实验性，不稳定）。
    yt-dlp 对抖音主页无 cookies 大概率失败；失败时返回 experimental=True 提示手动处理。
    """
    settings = settings or load_settings()
    base, ver_or_err = resolve_yt_dlp_base(settings)
    if base is None:
        return {"ok": False, "platform": "douyin", "error": _err("tool_missing", ver_or_err),
                "candidates": [], "author_name": "", "experimental": True}

    cmd = base + [
        "--flat-playlist", "--dump-json", "--no-warnings",
        "-I", f"1:{max_items}",
        url,
    ]
    code, out, err = _run_capture(cmd, SCAN_TIMEOUT)
    if code != 0 and not out.strip():
        return {
            "ok": False, "platform": "douyin", "experimental": True,
            "error": _err("scan_failed",
                          "抖音主页枚举失败（yt-dlp 对抖音主页支持不稳定，无 cookies 时通常无法枚举）。"
                          f"原始错误：{(err or out or f'returncode={code}')[:300]}"),
            "candidates": [], "author_name": "",
        }

    entries = _parse_flat_entries(out)
    if not entries:
        return {
            "ok": False, "platform": "douyin", "experimental": True,
            "error": _err("no_items_found",
                          "未枚举到任何视频。抖音主页通常需要登录态，建议手动下载后放入 auto_ingest_root。"),
            "candidates": [], "author_name": "",
        }

    candidates = [_entry_to_candidate(i + 1, e, "douyin") for i, e in enumerate(entries[:max_items])]
    author_name = next((c["author_name"] for c in candidates if c["author_name"]), "")
    return {
        "ok": True, "platform": "douyin", "experimental": True,
        "author_name": author_name,
        "total_seen": len(entries),
        "truncated": len(entries) > max_items,
        "candidates": candidates,
        "yt_dlp_version": ver_or_err,
        "error": "",
    }


def scan_homepage(
    url: str,
    max_items: int = DEFAULT_SCAN_OTHER,
    settings: dict | None = None,
) -> dict:
    """
    统一主页扫描入口，按 detect_homepage_platform 分发。
    返回结构：
      {ok, platform, author_name, total_seen, truncated,
       candidates, yt_dlp_version, experimental, error}
    """
    platform = detect_homepage_platform(url)
    _hard = HARD_SCAN_BILI if platform == "bilibili" else HARD_SCAN_OTHER
    max_items = max(1, min(int(max_items), _hard))
    if platform == "bilibili":
        return scan_bilibili_homepage(url, max_items, settings)
    if platform == "youtube":
        return scan_youtube_homepage(url, max_items, settings)
    if platform == "douyin":
        return scan_douyin_homepage(url, max_items, settings)
    return {
        "ok": False, "platform": "unknown", "experimental": False,
        "error": _err("url_unsupported",
                      "无法识别平台（支持：B站空间、YouTube 频道/播放列表、抖音主页）。"),
        "candidates": [], "author_name": "",
    }


# ─── 归档目录 ─────────────────────────────────────────────
def resolve_archive_dir(
    author_name: str,
    platform: str,
    auto_ingest_root: str,
    override_name: str = "",
) -> tuple[Path | None, str]:
    """
    生成并创建归档目录：<auto_ingest_root>/<account_name>__<platform>/
    override_name 非空时优先使用（管理员手动填写兜底）。
    account_name 为空或 unknown 时不创建目录，返回错误提示管理员填写。
    返回 (Path, error_msg)；失败时 Path=None。
    """
    root = Path(auto_ingest_root) if auto_ingest_root else None
    if not root:
        return None, "auto_ingest_root 未配置，请在「工具配置」设置主目录"

    raw_name = (override_name.strip() or author_name or "").strip()
    if not raw_name or raw_name.lower() == "unknown":
        return None, "账号名为空，请在下方手动填写归档账号名后再下载"

    account_safe = _safe_name(raw_name, 40)
    if not account_safe:
        return None, "账号名清洗后为空（含非法字符），请重新填写"

    folder_name = f"{account_safe}__{_safe_name(platform, 20)}"
    archive_dir = root / folder_name
    try:
        archive_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:  # noqa: BLE001
        return None, f"无法创建归档目录 {archive_dir}: {e}"
    return archive_dir, ""


# ─── 单条下载 ─────────────────────────────────────────────
def download_single_video(
    candidate: dict,
    archive_dir: Path,
    settings: dict | None = None,
    rate_limit: str = DEFAULT_RATE_LIMIT,
) -> dict:
    """
    下载单条候选视频到 archive_dir。
    文件名：<title_safe>_<platform>_<video_id>.<ext>
    返回 {ok, candidate_id, title, url, local_path, error}。
    """
    settings = settings or load_settings()
    base, ver_or_err = resolve_yt_dlp_base(settings)
    if base is None:
        return {
            "ok": False,
            "candidate_id": candidate.get("candidate_id", ""),
            "title": candidate.get("title", ""),
            "url": candidate.get("source_url", ""),
            "local_path": "",
            "error": f"yt-dlp 不可用：{ver_or_err}",
        }

    src_url = (candidate.get("source_url") or "").strip()
    if not src_url:
        return {
            "ok": False,
            "candidate_id": candidate.get("candidate_id", ""),
            "title": candidate.get("title", ""),
            "url": "",
            "local_path": "",
            "error": "source_url 为空，无法下载",
        }

    title_safe = _safe_name(candidate.get("title") or "video", 50)
    platform = _safe_name(candidate.get("platform") or "url", 15)
    vid = _safe_name(candidate.get("platform_item_id") or candidate.get("candidate_id") or "x", 20)
    stem = f"{title_safe}_{platform}_{vid}"
    out_tmpl = str(archive_dir / f"{stem}.%(ext)s")

    # 检查 ffmpeg 可用性以决定是否允许分离音视频合并
    settings = settings or load_settings()
    ffmpeg_exe = (settings.get("tools", {}).get("ffmpeg_exe") or "").strip() or "ffmpeg"
    _ff_code, _, _ = _run_capture([ffmpeg_exe, "-version"], 10)
    _ffmpeg_ok = (_ff_code == 0)

    quality = DEFAULT_QUALITY if _ffmpeg_ok else DEFAULT_QUALITY_NO_MERGE

    cmd = base + [
        "-f", quality,
        "--no-playlist",
        "--no-warnings",
        "--limit-rate", rate_limit,
        "-o", out_tmpl,
        src_url,
    ]
    code, out, err = _run_capture(cmd, DOWNLOAD_TIMEOUT)

    if code != 0:
        combined = (err or "") + (out or "")
        # ffmpeg 缺失但格式需要合并时的明确提示
        if not _ffmpeg_ok and re.search(
            r"(requires ffmpeg|ffmpeg not found|postprocessing|merger)", combined, re.IGNORECASE
        ):
            return {
                "ok": False,
                "candidate_id": candidate.get("candidate_id", ""),
                "title": candidate.get("title", ""),
                "url": src_url,
                "local_path": "",
                "error": "下载失败：yt-dlp 需要 ffmpeg 合并音视频流，但未检测到 ffmpeg。请在「工具配置」填写 ffmpeg_exe 路径。",
            }
        if _RESTRICTED_PATTERNS.search(combined):
            return {
                "ok": False,
                "candidate_id": candidate.get("candidate_id", ""),
                "title": candidate.get("title", ""),
                "url": src_url,
                "local_path": "",
                "error": "paid_or_restricted：付费/充电专属/大会员/区域限制视频，已跳过",
                "error_code": "not_accessible",
            }
        return {
            "ok": False,
            "candidate_id": candidate.get("candidate_id", ""),
            "title": candidate.get("title", ""),
            "url": src_url,
            "local_path": "",
            "error": (err or out or f"returncode={code}")[:500],
        }

    # 找输出文件（yt-dlp 决定扩展名，用 stem 前缀匹配）
    found = _find_downloaded_file(archive_dir, stem)
    if found is None:
        return {
            "ok": False,
            "candidate_id": candidate.get("candidate_id", ""),
            "title": candidate.get("title", ""),
            "url": src_url,
            "local_path": "",
            "error": "yt-dlp 返回 0 但未找到输出文件",
        }

    return {
        "ok": True,
        "candidate_id": candidate.get("candidate_id", ""),
        "title": candidate.get("title", ""),
        "url": src_url,
        "local_path": str(found),
        "error": "",
    }


_SIDECAR_EXTS = {
    ".part", ".ytdl", ".json", ".info", ".jpg", ".jpeg", ".png", ".webp",
    ".description", ".srt", ".vtt", ".txt", ".tmp",
}


def _find_downloaded_file(directory: Path, stem: str) -> Path | None:
    """在 directory 中找以 stem 开头、非 sidecar 的文件。"""
    for f in directory.iterdir():
        if f.is_file() and f.name.startswith(stem) and f.suffix.lower() not in _SIDECAR_EXTS:
            return f
    return None


# ─── 下载会话 + 报告 ──────────────────────────────────────
def generate_session_id(platform: str, author_name: str) -> str:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_author = _safe_name(author_name or "unknown", 20)
    safe_platform = _safe_name(platform or "unknown", 12)
    return f"pldl_{ts}_{safe_platform}_{safe_author}"


def _build_initial_report(
    session_id: str,
    platform: str,
    author_name: str,
    source_url: str,
    archive_dir: Path,
    candidates: list[dict],
) -> dict:
    return {
        "session_id": session_id,
        "platform": platform,
        "author_name": author_name,
        "source_url": source_url,
        "archive_dir": str(archive_dir),
        "started_at": _now_iso(),
        "finished_at": "",
        "total": len(candidates),
        "success": 0,
        "failed": 0,
        "skipped": 0,
        "items": [
            {
                # 运行时状态
                "status": "pending",
                "file_path": "",
                "error": "",
                "error_code": "",
                "retry_count": 0,
                "updated_at": _now_iso(),
                # 完整 candidate 字段（重试恢复用）
                "candidate_id": c.get("candidate_id", ""),
                "platform": c.get("platform", platform),
                "title": c.get("title", ""),
                "url": c.get("source_url") or c.get("url", ""),
                "source_url": c.get("source_url") or c.get("url", ""),
                "platform_item_id": c.get("platform_item_id", ""),
                "video_id": c.get("platform_item_id", ""),
                "author_name": c.get("author_name", author_name),
                "cover_url": c.get("cover_url", ""),
                "published_at": c.get("published_at", ""),
                "duration_sec": c.get("duration_sec", 0),
            }
            for c in candidates
        ],
    }


def candidate_from_report_item(item: dict) -> dict | None:
    """
    从 report item 重建可传给 download_single_video() 的 candidate dict。
    兼容旧报告（只有 url / video_id，无 source_url / platform_item_id）。
    返回 None 表示无法恢复（URL 为空）。
    """
    src = item.get("source_url") or item.get("url", "")
    if not src:
        return None
    return {
        "candidate_id": item.get("candidate_id", ""),
        "platform": item.get("platform", ""),
        "title": item.get("title", ""),
        "source_url": src,
        "url": src,
        "platform_item_id": item.get("platform_item_id") or item.get("video_id", ""),
        "author_name": item.get("author_name", ""),
        "cover_url": item.get("cover_url", ""),
        "published_at": item.get("published_at", ""),
        "duration_sec": item.get("duration_sec", 0),
    }


def write_report(report: dict, archive_dir: Path) -> str:
    """
    写 download_report.json 到 archive_dir。
    返回错误字符串，空字符串表示成功。
    """
    try:
        archive_dir.mkdir(parents=True, exist_ok=True)
        path = archive_dir / REPORT_FILENAME
        path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return ""
    except Exception as e:  # noqa: BLE001
        return f"write_report 失败：{e}"


def load_report(archive_dir: Path) -> dict | None:
    """
    从 archive_dir 加载 download_report.json。
    返回 dict 或 None（文件不存在 / 解析失败）。
    """
    try:
        path = archive_dir / REPORT_FILENAME
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


# ─── 批量下载 ─────────────────────────────────────────────
def batch_download(
    candidates: list[dict],
    archive_dir: Path,
    settings: dict | None = None,
    rate_limit: str = DEFAULT_RATE_LIMIT,
    progress_cb: Callable[[int, int, dict], None] | None = None,
    report: dict | None = None,
) -> dict:
    """
    批量下载候选列表到 archive_dir。
    progress_cb(done, total, last_result) 每条下载后回调（用于 UI 刷新）。
    report: 由调用方传入的初始报告 dict，本函数会就地更新并落盘。
    返回 {ok, total, success, failed, failures, archive_dir, finished_at}。
    """
    settings = settings or load_settings()
    total = len(candidates)
    success = 0
    failures: list[dict] = []

    # 建立 candidate_id → report item 的快速索引
    _item_map: dict[str, dict] = {}
    if report:
        for _it in report.get("items", []):
            _item_map[_it["candidate_id"]] = _it

    for i, cand in enumerate(candidates):
        cid = cand.get("candidate_id", "")

        # 标记 downloading
        if report and cid in _item_map:
            _item_map[cid]["status"] = "downloading"
            _item_map[cid]["updated_at"] = _now_iso()
            write_report(report, archive_dir)

        result = download_single_video(cand, archive_dir, settings, rate_limit)
        done = i + 1

        if result["ok"]:
            success += 1
            if report and cid in _item_map:
                _item_map[cid]["status"] = "success"
                _item_map[cid]["file_path"] = result.get("local_path", "")
                _item_map[cid]["error"] = ""
                _item_map[cid]["updated_at"] = _now_iso()
        else:
            error_code = result.get("error_code", "")
            failures.append({
                "candidate_id": cid,
                "title": result["title"],
                "url": result["url"],
                "error": result["error"],
                "error_code": error_code,
                "status": "failed",
                "retry_count": 0,
            })
            if report and cid in _item_map:
                _item_map[cid]["status"] = "failed"
                _item_map[cid]["error"] = result.get("error", "")
                _item_map[cid]["error_code"] = error_code
                _item_map[cid]["updated_at"] = _now_iso()

        if report:
            report["success"] = success
            report["failed"] = len(failures)
            write_report(report, archive_dir)

        if progress_cb:
            try:
                progress_cb(done, total, result)
            except Exception:  # noqa: BLE001
                pass

    finished_at = _now_iso()
    if report:
        report["finished_at"] = finished_at
        write_report(report, archive_dir)

    return {
        "ok": True,
        "total": total,
        "success": success,
        "failed": len(failures),
        "failures": failures,
        "archive_dir": str(archive_dir),
        "finished_at": finished_at,
    }
