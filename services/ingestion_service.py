"""
Ingestion Service — 数据导入与清洗
支持：原始文本、时间戳格式、JSON聊天记录、TXT理论资料
"""
import json
import re
from datetime import datetime
from database import save_message, add_knowledge, add_case, get_contact, add_contact


# ─── 时间戳格式解析（新增）────────────────────────────────────

_TS_HEADER = re.compile(
    r"^(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})\s+'(.+?)'$"
)
_VOICE_PREFIX = re.compile(r"^\[语音转文字\]\s*")


def parse_timestamp_chat(text: str, strip_voice_prefix: bool = True) -> tuple[list, str]:
    """
    解析时间戳格式聊天记录：
      2026-04-18 18:16:38 '我'
      消息内容（可多行）

      2026-04-18 18:16:47 'mayira'
      [语音转文字] 内容...

    返回 (messages, detected_contact_name)
    messages 每项：{"role": "me"/"her", "content": "...", "timestamp": "..."}
    detected_contact_name：自动从文本中识别出的对方昵称（取第一个非'我'的说话人）
    """
    messages = []
    detected_name = ""

    # 按空行切块，每块是一条消息
    blocks = re.split(r'\n\s*\n', text.strip())

    for block in blocks:
        block = block.strip()
        if not block:
            continue

        lines = block.splitlines()
        header_line = lines[0].strip()
        m = _TS_HEADER.match(header_line)
        if not m:
            # 不是标准头，跳过
            continue

        ts_str = m.group(1)
        speaker = m.group(2).strip()

        # 内容：第一行之后的所有行合并
        content_lines = [l.strip() for l in lines[1:] if l.strip()]
        content = " ".join(content_lines)

        if not content:
            continue

        # 去掉语音转文字前缀
        if strip_voice_prefix:
            content = _VOICE_PREFIX.sub("", content).strip()

        if not content:
            continue

        if speaker == "我":
            role = "me"
        else:
            role = "her"
            if not detected_name:
                detected_name = speaker

        messages.append({
            "role": role,
            "content": content,
            "timestamp": ts_str,
        })

    return messages, detected_name


def detect_contact_name_from_text(text: str) -> str:
    """从时间戳格式文本中提取对方昵称（第一个非'我'的说话人）"""
    for line in text.splitlines():
        m = _TS_HEADER.match(line.strip())
        if m:
            speaker = m.group(2).strip()
            if speaker != "我":
                return speaker
    return ""


# ─── 原有格式解析 ─────────────────────────────────────────────

def parse_raw_chat(text: str, contact_name: str) -> list:
    """
    解析简单格式：
      我: xxx / 她: xxx
      我：xxx / 她：xxx
    """
    messages = []
    for line in text.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        if line.startswith("我:") or line.startswith("我："):
            content = line[2:].strip()
            messages.append({"role": "me", "content": content})
        else:
            match = re.match(r'^(.+?)[：:]\s*(.+)$', line)
            if match:
                content = match.group(2).strip()
            else:
                content = line
            messages.append({"role": "her", "content": content})
    return messages


def parse_csv_chat(text: str) -> list:
    """解析标准CSV格式：timestamp,speaker,content"""
    messages = []
    for line in text.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        parts = line.split(",", 2)
        if len(parts) == 3:
            _, speaker, content = parts
            speaker = speaker.strip()
            role = "me" if speaker in ["我", "me", "Me", "ME"] else "her"
            messages.append({"role": role, "content": content.strip()})
    return messages


def parse_json_chat(json_text: str) -> list:
    """解析JSON格式聊天记录"""
    try:
        data = json.loads(json_text)
        messages = []
        for item in data:
            if "role" in item and "content" in item:
                messages.append({"role": item["role"], "content": item["content"]})
            elif "sender" in item:
                role = "me" if item["sender"] in ["我", "me"] else "her"
                content = item.get("text") or item.get("content") or ""
                messages.append({"role": role, "content": content})
        return messages
    except json.JSONDecodeError:
        return []


# ─── 数据库写入 ───────────────────────────────────────────────

def import_chat_to_db(contact_name: str, messages: list, source_type: str = "imported") -> int:
    """
    将解析后的消息批量写入数据库。
    messages 每项可含可选字段 timestamp。
    返回写入条数。
    """
    if not get_contact(contact_name):
        add_contact(contact_name)

    count = 0
    for msg in messages:
        content = msg.get("content", "").strip()
        if not content:
            continue
        save_message(contact_name, msg["role"], content, timestamp=msg.get("timestamp"))
        count += 1
    return count


# ─── 自动检测格式 ─────────────────────────────────────────────

def auto_detect_and_parse(text: str, contact_name: str = "") -> tuple[str, list]:
    """
    自动检测格式并解析，返回 (detected_format, messages)。
    优先级：时间戳格式 > JSON > CSV > 原始格式
    """
    text = text.strip()

    # 时间戳格式检测：第一个非空行匹配 YYYY-MM-DD HH:MM:SS '说话人'
    first_nonempty = next((l.strip() for l in text.splitlines() if l.strip()), "")
    if _TS_HEADER.match(first_nonempty):
        msgs, detected = parse_timestamp_chat(text)
        if msgs:
            # 如果调用方没传 contact_name，用检测到的昵称
            return "timestamp", msgs

    # JSON
    if text.startswith("[") or text.startswith("{"):
        msgs = parse_json_chat(text)
        if msgs:
            return "json", msgs

    # CSV（时间戳开头 + 逗号分隔）
    if re.match(r'^\d{4}-\d{2}-\d{2}', first_nonempty) and first_nonempty.count(",") >= 2:
        msgs = parse_csv_chat(text)
        if msgs:
            return "csv", msgs

    # 默认原始格式
    msgs = parse_raw_chat(text, contact_name)
    return "raw", msgs


# ─── 理论资料导入 ─────────────────────────────────────────────

def import_theory_txt(content: str, title: str = "", category: str = "developer_theory",
                      tags: list = None) -> int:
    """
    导入理论资料TXT，自动切块（按段落）。
    过滤少于30字的碎片块。
    返回写入块数。
    """
    chunks = [c.strip() for c in re.split(r'\n\s*\n', content) if c.strip()]
    chunks = [c for c in chunks if len(c) > 30]

    count = 0
    for i, chunk in enumerate(chunks):
        chunk_title = f"{title} - 第{i+1}段" if title else f"第{i+1}段"
        add_knowledge(category, chunk, chunk_title, tags or [])
        count += 1
    return count
