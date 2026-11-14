"""
Analysis Correction Service — 分析纠正记忆
按联系人持久化保存用户对 AI 分析的事实校正，存入 data/analysis_corrections.json
"""
import json
import os
from datetime import datetime

_CORRECTIONS_PATH = "data/analysis_corrections.json"


def _load_all() -> dict:
    if not os.path.exists(_CORRECTIONS_PATH):
        return {}
    try:
        with open(_CORRECTIONS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_all(data: dict):
    os.makedirs("data", exist_ok=True)
    with open(_CORRECTIONS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_corrections(contact_name: str) -> list:
    """返回该联系人的纠正列表，每项 {"time": ..., "correction": ...}"""
    data = _load_all()
    return data.get(contact_name, [])


def get_correction_hint(contact_name: str) -> str:
    """把该联系人所有纠正合并成一段 correction_hint 字符串，供 analyzer 使用"""
    corrections = get_corrections(contact_name)
    if not corrections:
        return ""
    lines = [c["correction"] for c in corrections if c.get("correction")]
    return "\n".join(lines)


def add_correction(contact_name: str, correction: str) -> tuple[bool, str]:
    """
    添加一条纠正。
    返回 (success, message)。
    空内容或明显重复不保存。
    """
    correction = correction.strip()
    if not correction:
        return False, "纠正内容不能为空"

    data = _load_all()
    existing = data.get(contact_name, [])

    # 避免明显重复（完全相同的文本）
    existing_texts = [c["correction"] for c in existing]
    if correction in existing_texts:
        return False, "该纠正内容已存在"

    existing.append({
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "correction": correction,
    })
    data[contact_name] = existing

    try:
        _save_all(data)
        return True, "纠正已保存"
    except Exception as e:
        return False, f"保存失败：{e}"


def clear_corrections(contact_name: str) -> tuple[bool, str]:
    """清空该联系人的所有纠正"""
    data = _load_all()
    if contact_name not in data:
        return False, "该联系人暂无纠正记录"
    data[contact_name] = []
    try:
        _save_all(data)
        return True, "纠正已清空"
    except Exception as e:
        return False, f"清空失败：{e}"


def is_corrections_file_healthy() -> bool:
    """检查 JSON 文件是否可正常读取"""
    if not os.path.exists(_CORRECTIONS_PATH):
        return True
    try:
        with open(_CORRECTIONS_PATH, "r", encoding="utf-8") as f:
            json.load(f)
        return True
    except Exception:
        return False
