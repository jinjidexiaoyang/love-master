"""
mentor_identity_service — 导师脱敏映射(后台真实身份 ↔ 前台化名)

边界:
  - 仅读写 data/mentor_ingest/admin_config/mentor_identity_map.json
  - 不改 cards.json / chunks.json / mentor_profiles.json
  - 不影响核心 Agent / 现有导师问答检索
  - 前台展示用 public_name + style_label,后台才能看到 real_mentor_name + 平台账号

数据形态:identity_map.json
{
  "version": 1,
  "identities": [
    {
      "public_mentor_id": "mentor_p001",
      "public_name": "灰狼",
      "style_label": "约会节奏控制型",
      "public_description": "...",
      "visibility": "public" | "hidden",
      "real_mentor_name": "张三",
      "platform": "douyin" | "bilibili" | "other" | "",
      "homepage_url": "",
      "account_id": "",
      "source_notes": "",
      "aggregate_courses": ["<课程名1>", ...]
    }
  ]
}
"""
from __future__ import annotations

import json
import re
from pathlib import Path

_IDENTITY_PATH = Path("data/mentor_ingest/admin_config/mentor_identity_map.json")


def _load() -> dict:
    if not _IDENTITY_PATH.exists():
        return {"version": 1, "identities": []}
    try:
        # 兼容带 BOM 的 utf-8 文件
        data = json.loads(_IDENTITY_PATH.read_text(encoding="utf-8-sig"))
        if isinstance(data, list):
            # 兼容老格式:整个文件就是 identities 数组
            return {"version": 1, "identities": data}
        if not isinstance(data, dict):
            return {"version": 1, "identities": []}
        if not isinstance(data.get("identities"), list):
            data["identities"] = []
        return data
    except Exception:
        return {"version": 1, "identities": []}


def _save(data: dict) -> None:
    _IDENTITY_PATH.parent.mkdir(parents=True, exist_ok=True)
    _IDENTITY_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def list_identities() -> list[dict]:
    return list(_load().get("identities") or [])


def get_by_public_id(public_id: str) -> dict | None:
    for it in list_identities():
        if it.get("public_mentor_id") == public_id:
            return it
    return None


def get_by_real_name(real_name: str) -> dict | None:
    if not real_name:
        return None
    for it in list_identities():
        if (it.get("real_mentor_name") or "") == real_name:
            return it
    return None


def get_by_course(course_name: str) -> dict | None:
    """通过 source_course 反查身份(真名聚合等场景用)"""
    if not course_name:
        return None
    for it in list_identities():
        if course_name in (it.get("aggregate_courses") or []):
            return it
    return None


def public_view(real_mentor_name: str | None = None,
                source_course: str | None = None) -> dict | None:
    """
    传入 cards 中的 mentor / source_course,返回前台可展示的脱敏视图:
      {public_mentor_id, public_name, style_label, public_description}
    找不到则返回 None,调用方按"未脱敏"处理(展示原 mentor 名或回落到 source_course)。
    """
    item = None
    if real_mentor_name:
        item = get_by_real_name(real_mentor_name)
    if item is None and source_course:
        item = get_by_course(source_course)
    if item is None:
        return None
    if (item.get("visibility") or "public") != "public":
        return None
    return {
        "public_mentor_id": item.get("public_mentor_id", ""),
        "public_name": item.get("public_name", ""),
        "style_label": item.get("style_label", ""),
        "public_description": item.get("public_description", ""),
    }


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _next_public_id(existing: list[dict]) -> str:
    nums = []
    for it in existing:
        m = re.match(r"mentor_p(\d+)$", it.get("public_mentor_id", "") or "")
        if m:
            nums.append(int(m.group(1)))
    n = (max(nums) + 1) if nums else 1
    return f"mentor_p{n:03d}"


def upsert_identity(
    public_name: str,
    style_label: str,
    real_mentor_name: str = "",
    public_description: str = "",
    visibility: str = "public",
    platform: str = "",
    homepage_url: str = "",
    account_id: str = "",
    source_notes: str = "",
    aggregate_courses: list[str] | None = None,
    public_mentor_id: str | None = None,
) -> dict:
    """新增或更新一条 identity。返回完整记录。public_mentor_id 缺省时按序生成。"""
    data = _load()
    items: list[dict] = data.get("identities") or []

    target = None
    if public_mentor_id:
        for it in items:
            if it.get("public_mentor_id") == public_mentor_id:
                target = it
                break

    if target is None:
        new_id = public_mentor_id or _next_public_id(items)
        target = {"public_mentor_id": new_id}
        items.append(target)

    target.update({
        "public_name": public_name.strip(),
        "style_label": style_label.strip(),
        "public_description": public_description.strip(),
        "visibility": visibility if visibility in ("public", "hidden") else "public",
        "real_mentor_name": real_mentor_name.strip(),
        "platform": platform.strip(),
        "homepage_url": homepage_url.strip(),
        "account_id": account_id.strip(),
        "source_notes": source_notes.strip(),
        "aggregate_courses": list(aggregate_courses or []),
    })
    data["identities"] = items
    _save(data)
    return target


def delete_identity(public_mentor_id: str) -> bool:
    data = _load()
    items: list[dict] = data.get("identities") or []
    new_items = [it for it in items if it.get("public_mentor_id") != public_mentor_id]
    if len(new_items) == len(items):
        return False
    data["identities"] = new_items
    _save(data)
    return True
