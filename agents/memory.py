"""
Memory — 联系人记忆模块（兼容层）
新架构中，analyzer.py 直接调用 database.py，此模块保留供外部调用
"""
from database import get_contact, get_chat_history, get_knowledge
from config import RELATIONSHIP_STAGES
import json


def get_contact_summary(name: str) -> str | None:
    contact = get_contact(name)
    if not contact:
        return None

    history = get_chat_history(name, limit=10)
    recent = "\n".join([
        f"{'我' if m['role'] == 'me' else '她'}：{m['content']}"
        for m in history
    ])

    lines = [
        f"=== 联系人：{name} ===",
        f"关系阶段：{contact.get('stage', '陌生人')}",
        f"好感度：{contact.get('interest_score', 0)}/10",
        f"暧昧指数：{contact.get('ambiguity_percent', 0)}%",
        f"势头：{contact.get('momentum', 'stable')}",
    ]
    for field, label in [("personality_tags", "性格"), ("interests", "兴趣")]:
        val = contact.get(field, "[]")
        try:
            items = json.loads(val) if isinstance(val, str) else val
            if items:
                lines.append(f"{label}：{', '.join(items)}")
        except Exception:
            pass
    if contact.get("communication_style"):
        lines.append(f"沟通风格：{contact['communication_style']}")
    if contact.get("last_analysis"):
        lines.append(f"最近分析：{contact['last_analysis']}")
    if recent:
        lines.append(f"\n【近期对话】\n{recent}")

    return "\n".join(lines)


def get_relevant_knowledge(technique_tags: list) -> str:
    all_knowledge = get_knowledge()
    relevant = []
    for item in all_knowledge:
        tags = json.loads(item.get("tags", "[]"))
        if any(tag in technique_tags for tag in tags):
            relevant.append(item["content"])
    return "\n\n".join(relevant[:3]) if relevant else ""


def get_stage_index(stage: str) -> int:
    try:
        return RELATIONSHIP_STAGES.index(stage)
    except ValueError:
        return 0
