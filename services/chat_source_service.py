"""
Chat Source Service — 通用消息格式转换层
provider-agnostic，负责时间戳规范化、内容清洗、标准消息格式定义
"""
from datetime import datetime, timezone


# 标准消息格式：
# {
#   "timestamp": "YYYY-MM-DD HH:MM:SS",   # 本地时间字符串
#   "sender": "me" | "her",
#   "content": str,
#   "source": str,          # 数据来源标识，如 "manual"
#   "external_id": str,     # 原始消息ID（可选）
#   "raw_type": int | str,  # 原始消息类型（可选）
# }


def normalize_timestamp(value) -> str:
    """
    将各种时间戳格式统一为 "YYYY-MM-DD HH:MM:SS"（本地时间）。
    支持：
      - unix 秒（int/float，10位）
      - unix 毫秒（int/float，13位）
      - 已是字符串则直接返回（截取前19字符）
    """
    if isinstance(value, str):
        return value[:19]

    value = float(value)
    # 毫秒判断：大于 1e12 认为是毫秒
    if value > 1e12:
        value = value / 1000.0

    dt = datetime.fromtimestamp(value)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def timestamp_str_to_unix(ts: str) -> int | None:
    """把 'YYYY-MM-DD HH:MM:SS' 转为 unix 秒（本地时区）。失败返回 None。"""
    if not ts:
        return None
    try:
        dt = datetime.strptime(ts[:19], "%Y-%m-%d %H:%M:%S")
        return int(dt.timestamp())
    except Exception:
        return None


def clean_message_content(content) -> str | None:
    """
    清洗消息内容：去首尾空白，空内容返回 None。
    """
    if not content:
        return None
    s = str(content).strip()
    return s if s else None


def build_timestamp_chat_text(messages: list, my_name: str = "我") -> str:
    """
    将标准消息列表转换为 ingestion_service 可识别的时间戳格式文本：

      2026-04-18 18:16:38 '我'
      消息内容

      2026-04-18 18:16:47 'mayira'
      消息内容

    messages 每项须含 timestamp / sender / content。
    sender == "me" → my_name，sender == "her" → contact_display_name（由调用方在 content 里已处理）
    """
    lines = []
    for msg in messages:
        ts = msg.get("timestamp", "")
        sender = msg.get("sender", "her")
        content = msg.get("content", "")
        speaker = my_name if sender == "me" else msg.get("contact_name", "her")
        lines.append(f"{ts} '{speaker}'")
        lines.append(content)
        lines.append("")  # 空行分隔
    return "\n".join(lines)
