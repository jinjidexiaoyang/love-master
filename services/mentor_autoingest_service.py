"""
mentor_autoingest_service — P4C 自动本地入库:文件夹名解析 + 主目录扫描候选(Auto-1)
                            + 候选创建 batch(Auto-2A)
                            + 导师身份建议 LLM(Auto-10)

职责:
  - 解析主素材目录下子文件夹名(真实导师名__前台化名__风格标签__平台)
  - 扫描主目录一级子文件夹,生成候选(candidate)列表供管理端展示
  - 只读 mentor_identity_map 做已有导师匹配,绝不写入
  - 把 ready / needs_info 候选创建为 ingest batch(Auto-2A)
  - suggest_mentor_identity：仅在管理员点击时调用 LLM 生成建议；无 key 或调用失败时回退 mock
  - platform 只作后台候选信息,不进入用户端展示

边界:
  - 只读 data/mentor_ingest/admin_config/mentor_identity_map.json(经 mentor_identity_service)
  - 批次写入仅限 staging/batches/<batch_id>/ 目录
  - 不触碰正式 cards/chunks/profiles、不碰核心 Agent、不写 mentor_identity_map
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime
from pathlib import Path

from services import mentor_identity_service as ident

# ─── 命名解析 ─────────────────────────────────────────────
FOLDER_NAME_SEP = "__"
PLATFORM_WHITELIST = ("bilibili", "douyin", "xiaohongshu", "local", "unknown")

# ─── 扫描支持的文件类型(Auto-1 固定集合,与命名解析同模块自洽)──
VIDEO_EXTS = (".mp4", ".mov", ".mkv", ".avi")
AUDIO_EXTS = (".mp3", ".wav", ".m4a")
TEXT_EXTS = (".txt", ".md")

# ─── slug 清洗正则(与 mentor_ingest_service 保持一致) ─────
_SAFE_RE = re.compile(r"[^A-Za-z0-9一-鿿_\-]")


def parse_folder_name(name: str) -> dict:
    """
    解析文件夹名 → 导师字段。

    完整格式:真实导师名__前台化名__风格标签__平台
      - 4 段:全部填充
      - 3 段:real_mentor + public_name + style_label,platform=unknown
      - 1 段:仅 real_mentor,其余空,platform=unknown

    简化格式(Auto-9):平台网名__平台
      - 2 段且第二段是平台白名单成员:
        real_mentor="", public_name=第一段, style_label="", is_simplified=True

    普通 2 段(非简化):real_mentor + public_name,style_label="",platform=unknown
    platform 白名单:bilibili / douyin / xiaohongshu / local / unknown;
    非白名单归一为 unknown 并给 warning。
    """
    raw = (name or "").strip()
    parts = [p.strip() for p in raw.split(FOLDER_NAME_SEP)]
    while parts and parts[-1] == "":
        parts.pop()

    is_simplified = False

    # ── 简化格式检测：2 段且第二段是平台白名单 ──────────────
    if len(parts) == 2 and parts[1].lower() in PLATFORM_WHITELIST:
        real_mentor  = ""
        public_name  = parts[0]
        style_label  = ""
        platform_raw = parts[1]
        is_simplified = True
    else:
        real_mentor  = parts[0] if len(parts) >= 1 else ""
        public_name  = parts[1] if len(parts) >= 2 else ""
        style_label  = parts[2] if len(parts) >= 3 else ""
        platform_raw = parts[3] if len(parts) >= 4 else ""

    platform_warning = ""
    platform = (platform_raw or "").lower()
    if not platform:
        platform = "unknown"
    elif platform not in PLATFORM_WHITELIST:
        platform_warning = (
            f"platform '{platform_raw}' 不在白名单 {PLATFORM_WHITELIST},已归一为 unknown"
        )
        platform = "unknown"

    return {
        "real_mentor":     real_mentor,
        "public_name":     public_name,
        "style_label":     style_label,
        "platform":        platform,
        "platform_warning": platform_warning,
        "segment_count":   len(parts),
        "is_simplified":   is_simplified,
    }


def _match_identity(real_mentor: str, public_name: str) -> dict | None:
    """
    只读匹配已有导师:real_mentor 精确 → public_name 精确 → None。
    返回原始 identity dict(只读),不修改。
    """
    if real_mentor:
        hit = ident.get_by_real_name(real_mentor)
        if hit:
            return hit
    if public_name:
        for it in ident.list_identities():
            if (it.get("public_name") or "") == public_name:
                return it
    return None


def _count_supported_files(folder: Path) -> dict:
    """递归统计文件夹下支持的视频/音频/文本数量。"""
    video = audio = text = 0
    for p in folder.rglob("*"):
        if not p.is_file():
            continue
        ext = p.suffix.lower()
        if ext in VIDEO_EXTS:
            video += 1
        elif ext in AUDIO_EXTS:
            audio += 1
        elif ext in TEXT_EXTS:
            text += 1
    return {"video": video, "audio": audio, "text": text}


def _candidate_id(folder_name: str, index: int) -> str:
    safe = "".join(ch if ch.isalnum() else "_" for ch in folder_name)[:32].strip("_")
    return f"cand_{index:03d}_{safe or 'x'}"


def _build_matched_view(matched: dict | None, parsed: dict) -> tuple[dict | None, list[str]]:
    """
    把命中的 identity 整理成管理端展示视图(只读),并产出"可继承"提示。
    matched_identity 返回结构只暴露管理员端需要的字段(含 platform,仅后台)。
    """
    if not matched:
        return None, []
    warnings: list[str] = []
    view = {
        "public_mentor_id": matched.get("public_mentor_id", ""),
        "public_name": matched.get("public_name", ""),
        "style_label": matched.get("style_label", ""),
        "real_mentor_name": matched.get("real_mentor_name", ""),
        "platform": matched.get("platform", ""),  # 仅后台展示
        "visibility": matched.get("visibility", "public"),
        "aggregate_courses": list(matched.get("aggregate_courses") or []),
        "can_inherit_public_name": False,
        "can_inherit_style_label": False,
    }
    if not parsed.get("public_name") and view["public_name"]:
        view["can_inherit_public_name"] = True
        warnings.append(f"文件夹未给 public_name,可继承已有导师「{view['public_name']}」")
    if not parsed.get("style_label") and view["style_label"]:
        view["can_inherit_style_label"] = True
        warnings.append(f"文件夹未给 style_label,可继承已有导师「{view['style_label']}」")
    return view, warnings


def scan_auto_ingest_root(root_path: str | Path) -> dict:
    """
    扫描主素材目录 root_path 下的一级子文件夹,每个子文件夹作为一个 candidate。
    文件数量递归统计,但 candidate 只取一级子文件夹。
    只读 identity_map 做匹配,不写任何文件、不创建 batch。
    返回 {ok, root, exists, candidates, warnings}。
    """
    root = Path(root_path) if root_path else None
    result: dict = {
        "ok": True,
        "root": str(root) if root else "",
        "exists": False,
        "candidates": [],
        "warnings": [],
    }
    if not root:
        result["ok"] = False
        result["warnings"].append("未配置主目录路径(auto_ingest_root)")
        return result
    if not root.exists() or not root.is_dir():
        # 非 fatal:只 warning
        result["warnings"].append(f"主目录不存在或不是目录: {root}")
        return result

    result["exists"] = True
    candidates: list[dict] = []
    index = 0
    for sub in sorted(root.iterdir()):
        if not sub.is_dir():
            continue
        index += 1
        folder_name = sub.name
        parsed = parse_folder_name(folder_name)
        counts = _count_supported_files(sub)
        total = counts["video"] + counts["audio"] + counts["text"]

        warnings: list[str] = []
        if parsed["platform_warning"]:
            warnings.append(parsed["platform_warning"])

        matched = _match_identity(parsed["real_mentor"], parsed["public_name"])
        matched_view, inherit_warnings = _build_matched_view(matched, parsed)
        warnings.extend(inherit_warnings)

        # 状态判定:
        #   invalid     — 连真实导师名和平台网名都解析不出（1段空名）
        #   empty       — 没有任何受支持素材文件
        #   needs_info  — 简化格式（平台网名__平台）或 public_name 缺失且无命中可继承
        #   ready       — 有素材,且 public_name 已知或可从命中导师继承
        is_simplified = parsed.get("is_simplified", False)
        if not parsed["real_mentor"] and not parsed["public_name"]:
            status = "invalid"
            warnings.append("无法从文件夹名解析真实导师名")
        elif total == 0:
            status = "empty"
            warnings.append("文件夹内无受支持的视频/音频/文本文件")
        elif is_simplified:
            status = "needs_info"
            warnings.append("简化命名（平台网名__平台），导师身份待确认：需补充真实导师名或选择/创建导师身份")
        else:
            has_public = bool(parsed["public_name"]) or (
                matched_view and matched_view.get("can_inherit_public_name")
            ) or bool(matched_view)
            status = "ready" if has_public else "needs_info"
            if status == "needs_info":
                warnings.append("缺少前台化名(public_name),需在后台补全或选择已有导师")

        candidates.append({
            "candidate_id": _candidate_id(folder_name, index),
            "folder_name": folder_name,
            "folder_path": str(sub),
            "parsed_real_mentor": parsed["real_mentor"],
            "parsed_public_name": parsed["public_name"],
            "parsed_style_label": parsed["style_label"],
            "parsed_platform": parsed["platform"],
            "platform_warning": parsed["platform_warning"],
            "is_simplified": is_simplified,
            "video_count": counts["video"],
            "audio_count": counts["audio"],
            "text_count": counts["text"],
            "total_supported_count": total,
            "status": status,
            "warnings": warnings,
            "matched_identity": matched_view,
        })

    result["candidates"] = candidates
    if not candidates:
        result["warnings"].append("主目录下没有任何子文件夹")
    return result


# ─── Auto-8/10: 导师身份建议(LLM + mock fallback) ────────

def _mock_suggest_mentor_identity(candidate: dict) -> dict:
    """确定性 mock 建议，作为 LLM 不可用时的 fallback。"""
    parsed_public = (candidate.get("parsed_public_name") or "").strip()
    parsed_real   = (candidate.get("parsed_real_mentor") or "").strip()
    parsed_style  = (candidate.get("parsed_style_label") or "").strip()
    platform      = (candidate.get("parsed_platform") or "unknown").strip()

    suggested_public = parsed_public or parsed_real or "未知化名"
    suggested_style  = parsed_style or ""

    if not suggested_style:
        _platform_style_hint = {
            "bilibili": "长视频内容型",
            "douyin":   "短视频快节奏型",
            "local":    "线下课程型",
            "unknown":  "风格待定",
        }
        suggested_style = _platform_style_hint.get(platform, "风格待定")

    existing = ident.list_identities()
    conflict_warnings: list[str] = []
    existing_public_names = [
        (it.get("public_name") or "").strip()
        for it in existing
        if (it.get("public_name") or "").strip()
    ]
    existing_style_labels = [
        (it.get("style_label") or "").strip()
        for it in existing
        if (it.get("style_label") or "").strip()
    ]

    if suggested_public in existing_public_names:
        disambig = (
            f"{suggested_public}({parsed_real or platform})"
            if (parsed_real or platform != "unknown")
            else f"{suggested_public}_新"
        )
        conflict_warnings.append(
            f"public_name「{suggested_public}」与已有导师重名，建议改为「{disambig}」"
        )
        suggested_public = disambig

    if suggested_style in existing_style_labels:
        conflict_warnings.append(
            f"style_label「{suggested_style}」已被已有导师使用，建议补充差异化描述以区分"
        )

    parts: list[str] = []
    if parsed_public:
        parts.append(f"文件夹已给出前台化名「{parsed_public}」")
    elif parsed_real:
        parts.append(f"文件夹未给前台化名，以真实导师名「{parsed_real}」作为初始建议")
    if parsed_style:
        parts.append(f"文件夹已给出风格标签「{parsed_style}」")
    else:
        parts.append(f"文件夹未给风格标签，按 platform={platform} 给出占位建议「{suggested_style}」")
    if conflict_warnings:
        parts.append("检测到与已有导师冲突，已自动追加差异化提示")

    reasoning = "；".join(parts) + "。" if parts else "基于文件夹信息生成初始建议。"

    return {
        "ok": True,
        "suggested_public_name": suggested_public,
        "suggested_style_label": suggested_style,
        "reasoning": reasoning,
        "conflict_warnings": conflict_warnings,
        "source": "mock",
        "error": "",
    }


def _llm_suggest_mentor_identity(candidate: dict) -> dict:
    """
    调用 DeepSeek 兼容 API 生成导师身份建议。
    返回与 _mock_suggest_mentor_identity 相同结构，source="llm"。
    抛出异常由调用方处理。
    """
    import json as _json

    try:
        from openai import OpenAI as _OpenAI
    except ImportError:
        raise RuntimeError("openai 包未安装，无法调用 DeepSeek API")

    from config import get_deepseek_config as _get_ds_cfg
    _ds = _get_ds_cfg()
    api_key  = _ds.api_key
    base_url = _ds.base_url
    model    = _ds.model

    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY 未配置")

    # 已有导师列表（仅暴露非敏感字段）
    existing = ident.list_identities()
    existing_summary = [
        {
            "public_mentor_id": it.get("public_mentor_id", ""),
            "public_name":      it.get("public_name", ""),
            "style_label":      it.get("style_label", ""),
            "real_mentor":      it.get("real_mentor_name", ""),
            "platform":         it.get("platform", ""),
        }
        for it in existing
    ]

    prompt = f"""你是一个帮助后台管理员为新导师生成脱敏公开身份的 AI 助手。

## 新候选信息
- folder_name: {candidate.get('folder_name', '')}
- parsed_real_mentor: {candidate.get('parsed_real_mentor', '') or '(未知)'}
- parsed_public_name: {candidate.get('parsed_public_name', '') or '(未给)'}
- parsed_style_label: {candidate.get('parsed_style_label', '') or '(未给)'}
- parsed_platform: {candidate.get('parsed_platform', 'unknown')}
- video_count: {candidate.get('video_count', 0)}
- audio_count: {candidate.get('audio_count', 0)}
- text_count: {candidate.get('text_count', 0)}

## 已有导师列表
{_json.dumps(existing_summary, ensure_ascii=False, indent=2)}

## 要求
1. 生成 suggested_public_name：脱敏前台化名，不能暴露真实姓名，简短易记，与已有 public_name 差异化。
2. 生成 suggested_style_label：风格标签，简短（≤10字），用户端可展示，与已有 style_label 差异化。
3. reasoning：简明说明命名依据（1-2句）。
4. conflict_warnings：列出潜在冲突，如疑似同一导师请提示"建议加入已有导师 [public_mentor_id] 而非新建"。若无冲突返回空数组。

## 输出格式（严格 JSON，不附加任何其他文字）
{{
  "suggested_public_name": "...",
  "suggested_style_label": "...",
  "reasoning": "...",
  "conflict_warnings": ["..."]
}}"""

    client = _OpenAI(api_key=api_key, base_url=base_url)
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
        max_tokens=512,
    )
    raw = (resp.choices[0].message.content or "").strip()

    # 去掉可能的 markdown 代码块包裹
    if raw.startswith("```"):
        raw = raw.split("```", 2)[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    if raw.endswith("```"):
        raw = raw[:-3].strip()

    parsed = _json.loads(raw)

    return {
        "ok": True,
        "suggested_public_name": str(parsed.get("suggested_public_name", "")),
        "suggested_style_label": str(parsed.get("suggested_style_label", "")),
        "reasoning":             str(parsed.get("reasoning", "")),
        "conflict_warnings":     list(parsed.get("conflict_warnings") or []),
        "source": "llm",
        "error": "",
    }


def suggest_mentor_identity(candidate: dict) -> dict:
    """
    为未命中 matched_identity 的候选生成导师身份建议（Auto-10）。

    - 若 DEEPSEEK_API_KEY 存在且 openai 包可用，调用 DeepSeek LLM。
    - LLM 调用失败时回退到 deterministic mock，并在 conflict_warnings 加 warning。
    - 不写 mentor_identity_map，不创建 batch。

    返回:
      {
        "ok": True,
        "suggested_public_name": str,
        "suggested_style_label": str,
        "reasoning": str,
        "conflict_warnings": list[str],
        "source": "llm" | "mock" | "mock_fallback",
        "error": str,
      }
    """
    from config import get_deepseek_config as _get_ds_cfg
    api_key = _get_ds_cfg().api_key.strip()

    if api_key:
        try:
            return _llm_suggest_mentor_identity(candidate)
        except Exception as exc:
            fallback = _mock_suggest_mentor_identity(candidate)
            fallback["source"] = "mock_fallback"
            fallback["conflict_warnings"] = list(fallback.get("conflict_warnings") or [])
            fallback["conflict_warnings"].insert(
                0, f"⚠ LLM 调用失败（{exc}），已回退到本地建议"
            )
            return fallback

    return _mock_suggest_mentor_identity(candidate)


# ─── Auto-2A: 候选创建 batch ──────────────────────────────
def create_auto_ingest_batch(
    candidate: dict,
    mentor_identity: dict | None = None,
) -> dict:
    """
    把 ready / needs_info 候选文件夹创建为待处理 ingest batch。

    参数:
      candidate       — scan_auto_ingest_root 返回的 candidate dict
      mentor_identity — 可选,显式传入的 identity dict(来自 mentor_identity_service);
                        优先级高于 candidate["matched_identity"]

    返回:
      {ok, batch_id, batch_dir, staging_dir, error, warnings}

    约束:
      - status 为 empty / invalid 时拒绝创建
      - folder_path 不存在时拒绝创建
      - needs_info 可创建,但 warnings 中注明"导师身份待确认"
      - 不写 mentor_identity_map,不 approve,不转写,不卡片化
    """
    # 延迟导入,避免循环依赖(mentor_ingest_service 独立于 autoingest)
    from services import mentor_ingest_service as ingest

    warnings: list[str] = []

    # ── 1. 状态校验 ──────────────────────────────────────────
    cand_status = candidate.get("status", "")
    if cand_status in ("empty", "invalid"):
        return {
            "ok": False,
            "batch_id": "",
            "batch_dir": "",
            "staging_dir": str(ingest.STAGING_ROOT),
            "error": f"候选状态为 '{cand_status}'，不允许创建 batch",
            "warnings": [],
        }

    # ── 2. folder_path 校验 ──────────────────────────────────
    folder_path = Path(candidate.get("folder_path", ""))
    if not folder_path.exists() or not folder_path.is_dir():
        return {
            "ok": False,
            "batch_id": "",
            "batch_dir": "",
            "staging_dir": str(ingest.STAGING_ROOT),
            "error": f"素材文件夹不存在: {folder_path}",
            "warnings": [],
        }

    # ── 3. needs_info 警告 ───────────────────────────────────
    if cand_status == "needs_info":
        warnings.append(
            "导师身份待确认：public_name 缺失或尚未匹配已有导师，建议在后台补全后再进行转写"
        )

    # ── 4. 解析身份字段(mentor_identity > matched_identity > parsed) ──
    resolved = mentor_identity or candidate.get("matched_identity") or {}
    real_mentor_name = (
        resolved.get("real_mentor_name")
        or candidate.get("parsed_real_mentor")
        or ""
    )
    public_mentor_id = resolved.get("public_mentor_id") or ""
    public_name = (
        resolved.get("public_name")
        or candidate.get("parsed_public_name")
        or ""
    )
    style_label = (
        resolved.get("style_label")
        or candidate.get("parsed_style_label")
        or ""
    )
    platform = candidate.get("parsed_platform") or "unknown"
    folder_name = candidate.get("folder_name") or folder_path.name

    # ── 5. 生成 batch_id: auto_YYYYMMDD_HHMMSS_slug ─────────
    now_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    slug_src = real_mentor_name or public_name or folder_name or "x"
    slug = _SAFE_RE.sub("_", slug_src)
    slug = re.sub(r"_+", "_", slug).strip("_")[:20] or "x"
    batch_id = f"auto_{now_str}_{slug}"

    # 冲突检测:追加短 uuid 后缀
    if (ingest.STAGING_ROOT / batch_id).exists():
        suffix = uuid.uuid4().hex[:6]
        batch_id = f"{batch_id}_{suffix}"

    # ── 6. 创建 batch 目录结构 ───────────────────────────────
    bd = ingest.batch_dir(batch_id)
    for sub in ("videos", "audio", "transcripts", "cleaned"):
        (bd / sub).mkdir(parents=True, exist_ok=True)

    # ── 7. 写入初始 manifest ─────────────────────────────────
    now_iso = ingest._now_iso()
    manifest = {
        "batch_id": batch_id,
        "source_type": "auto_ingest_folder",
        "source_folder": str(folder_path),
        "folder_name": folder_name,
        "real_mentor_name": real_mentor_name,
        "public_mentor_id": public_mentor_id,
        "public_name": public_name,
        "style_label": style_label,
        "platform": platform,
        "video_count": candidate.get("video_count", 0),
        "audio_count": candidate.get("audio_count", 0),
        "text_count": candidate.get("text_count", 0),
        "total_supported_count": candidate.get("total_supported_count", 0),
        "status": "created",
        "created_at": now_iso,
        "updated_at": now_iso,
        "next_step": "transcribe",
        "status_history": [
            {"status": "created", "at": now_iso, "note": "auto_ingest batch created"}
        ],
        "stats": {"item_count": 0, "transcribed": 0, "carded": 0},
        "warnings": warnings,
        "candidate_warnings": list(candidate.get("warnings") or []),
    }
    ingest.save_manifest(batch_id, manifest)
    ingest.save_source_items(batch_id, [])

    # ── 8. 扫描素材文件夹,生成 source_items ─────────────────
    # scan_local_folder 会把 manifest.status 推进到 "scanned"
    scan_result = ingest.scan_local_folder(batch_id, folder_path)
    if not scan_result.get("ok"):
        warnings.append(
            f"扫描素材文件夹时出现问题: {scan_result.get('error', '未知错误')}"
        )

    return {
        "ok": True,
        "batch_id": batch_id,
        "batch_dir": str(bd),
        "staging_dir": str(ingest.STAGING_ROOT),
        "error": "",
        "warnings": warnings,
    }
