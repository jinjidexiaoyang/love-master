"""
Analyzer — 单轮分析链路
输入：联系人名 + 新消息列表
输出：结构化JSON分析结果，同时更新DB
"""
import json
from config import MODEL, MAX_TOKENS, RELATIONSHIP_STAGES
from database import (get_contact, get_chat_history, update_contact_profile,
                      update_relationship_state, save_metrics)
from services.style_engine import get_style_engine
from services.anthropic_client import get_client

client = get_client()


def _clean_json_newlines(s: str) -> str:
    """将 JSON 字符串值内部的换行符替换为空格，避免解析失败"""
    result = []
    in_string = False
    escape_next = False
    for ch in s:
        if escape_next:
            result.append(ch)
            escape_next = False
        elif ch == '\\':
            result.append(ch)
            escape_next = True
        elif ch == '"':
            result.append(ch)
            in_string = not in_string
        elif ch in ('\n', '\r') and in_string:
            result.append(' ')
        else:
            result.append(ch)
    return ''.join(result)


def _build_contact_context(contact_name: str) -> str:
    contact = get_contact(contact_name)
    if not contact:
        return f"联系人：{contact_name}（新联系人，暂无资料）"

    lines = [f"联系人：{contact_name}"]
    if contact.get("nickname"):
        lines.append(f"昵称：{contact['nickname']}")
    lines.append(f"关系阶段：{contact.get('stage', '陌生人')}")
    lines.append(f"当前好感度：{contact.get('interest_score', 0)}/10")
    lines.append(f"暧昧指数：{contact.get('ambiguity_percent', 0)}%")

    for field, label in [("personality_tags", "性格标签"), ("interests", "兴趣爱好")]:
        val = contact.get(field, "[]")
        try:
            items = json.loads(val) if isinstance(val, str) else val
            if items:
                lines.append(f"{label}：{', '.join(items)}")
        except Exception:
            pass

    if contact.get("communication_style"):
        lines.append(f"沟通风格：{contact['communication_style']}")
    if contact.get("mbti"):
        lines.append(f"MBTI：{contact['mbti']}")

    return "\n".join(lines)


def _build_history_context(contact_name: str, limit: int = 10) -> str:
    history = get_chat_history(contact_name, limit=limit)
    if not history:
        return "（暂无历史记录）"
    lines = []
    for msg in history:
        role_label = "她" if msg["role"] == "her" else "我"
        lines.append(f"{role_label}：{msg['content']}")
    return "\n".join(lines)


def analyze_conversation(contact_name: str, new_messages: list,
                         correction_hint: str = "") -> dict:
    """
    完整分析一轮对话
    new_messages: [{"role": "her"/"me", "content": "..."}]
    correction_hint: 用户对上一轮分析的事实校正，非空时注入 prompt 高优先级块
    """
    engine = get_style_engine()
    contact_ctx = _build_contact_context(contact_name)
    history_ctx = _build_history_context(contact_name)

    new_msgs_text = "\n".join([
        f"{'她' if m['role'] == 'her' else '我'}：{m['content']}"
        for m in new_messages
    ])

    # 取当前关系阶段，用于注入对应 stage rules
    contact = get_contact(contact_name)
    current_stage = contact.get("stage", "stranger") if contact else "stranger"
    _stage_map = {
        "陌生人": "stranger", "认识": "acquaintance", "熟人": "familiar",
        "暧昧": "ambiguous", "上头": "intimate", "痴迷": "intimate",
    }
    stage_key = _stage_map.get(current_stage, "stranger")

    system_prompt = engine.inject(
        base_prompt="""你是实战型关系策略顾问，擅长博弈论、IOI/IOD信号识别、关系势能判断。
只输出JSON，不要任何额外文字。

【分析风格要求】
- 专业但不啰嗦；有术语但每句话都要有信息量
- 不要心理咨询腔，不要大段论文式表述
- 像有实战经验的人在给具体判断，不像在写报告
- 保留专业词：试探、框架、自证、张力、主导权、关系势能、IOI/IOD
- 但每个判断后面必须给出可执行方向，不能只堆术语

【分析维度】
IOI信号：主动联系、提问、分享私事、撒娇、回复速度快、用表情包
IOD信号：回复敷衍、话题终结、长时间不回、单字回复、转移话题

【输出格式要求】
analysis_summary：3-5句，每句都要有实际信息量，控制在120-220字
  - 说清楚当前局面的关键矛盾
  - 说清楚对方的心理状态或行为动机
  - 给出判断方向，不要含糊收尾
recommended_techniques / opportunities：判断 + 可执行动作，不要只写技巧名
red_flags / avoid_techniques：具体说明如果做了会有什么后果
ioi_signals / iod_signals：每条不超过20字，直接说信号本身""",
        include_cases=False,
        stage=stage_key,
    )

    correction_block = ""
    if correction_hint and correction_hint.strip():
        correction_block = f"""
【用户纠正/事实校正】
以下内容来自用户本人，是对上一轮分析或聊天语境的修正。
必须优先采纳。不要再沿用被纠正的错误判断。
如果聊天内容与纠正冲突，以纠正为准。
但不要机械复述纠正内容，要自然融入关系判断。

纠正内容：
{correction_hint.strip()}

"""

    user_prompt = f"""【联系人档案】
{contact_ctx}

【近期聊天记录】
{history_ctx}
{correction_block}
【本轮新消息】
{new_msgs_text}

输出JSON（所有字段必填）：
{{
  "personality_tags": ["标签1", "标签2"],
  "interests": ["兴趣1"],
  "communication_style": "沟通风格描述",
  "mbti": "推测MBTI或空字符串",
  "relationship_stage": "陌生人/认识/熟人/暧昧/上头/痴迷",
  "ambiguity_percent": 0,
  "interest_score": 0.0,
  "momentum": "rising/stable/falling",
  "ioi_signals": ["每条≤20字的正向信号"],
  "iod_signals": ["每条≤20字的风险信号"],
  "recommended_techniques": ["技巧名：适用条件和执行方式（一句话）"],
  "avoid_techniques": ["不要做的事：后果说明（一句话）"],
  "red_flags": ["具体风险点，说明后果"],
  "opportunities": ["判断+可执行动作，不要只写方向名"],
  "analysis_summary": "3-5句实战判断，120-220字，有判断有动作，不要心理咨询腔。注意：所有字符串值必须在同一行，不能包含换行符"
}}"""

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=3000,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}]
        )
        raw = response.content[0].text.strip()
        if "```json" in raw:
            raw = raw.split("```json")[1].split("```")[0].strip()
        elif "```" in raw:
            raw = raw.split("```")[1].split("```")[0].strip()
        raw = _clean_json_newlines(raw)

        result = json.loads(raw)
        # 取本轮消息里最后一条的时间戳作为打点时间
        msg_ts = None
        for m in reversed(new_messages):
            if m.get("timestamp"):
                msg_ts = m["timestamp"]
                break
        _persist_analysis(contact_name, result, msg_timestamp=msg_ts)
        return result

    except json.JSONDecodeError as e:
        return {"error": f"JSON解析失败: {e}"}
    except Exception as e:
        return {"error": str(e)}


def _persist_analysis(contact_name: str, result: dict, msg_timestamp: str = None):
    profile_fields = {}
    for f in ["personality_tags", "interests", "communication_style", "mbti"]:
        if result.get(f):
            profile_fields[f] = result[f]
    if profile_fields:
        update_contact_profile(contact_name, **profile_fields)

    state_fields = {}
    if result.get("relationship_stage"):
        state_fields["stage"] = result["relationship_stage"]
    if result.get("ambiguity_percent") is not None:
        state_fields["ambiguity_percent"] = result["ambiguity_percent"]
    if result.get("interest_score") is not None:
        state_fields["interest_score"] = result["interest_score"]
    if result.get("momentum"):
        state_fields["momentum"] = result["momentum"]
    if result.get("analysis_summary"):
        state_fields["last_analysis"] = result["analysis_summary"]
    if state_fields:
        update_relationship_state(contact_name, **state_fields)

    if result.get("relationship_stage") and result.get("interest_score") is not None:
        save_metrics(
            contact_name,
            result.get("ambiguity_percent", 0),
            result.get("interest_score", 0),
            result["relationship_stage"],
            timestamp=msg_timestamp
        )


def quick_analyze_message(contact_name: str, single_message: str) -> dict:
    """快速分析单条消息的IOI/IOD信号"""
    system_prompt = "你是IOI/IOD信号识别专家。只输出JSON，不要额外文字。"

    user_prompt = f"""联系人：{contact_name}
她说：{single_message}

输出JSON：
{{
  "ioi_signals": ["信号1"],
  "iod_signals": ["信号1"],
  "sentiment": "positive/neutral/negative",
  "urgency": "high/medium/low",
  "quick_take": "10字以内判断"
}}"""

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=512,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}]
        )
        raw = response.content[0].text.strip()
        if "```" in raw:
            raw = raw.split("```")[1].split("```")[0].strip()
            if raw.startswith("json"):
                raw = raw[4:].strip()
        return json.loads(raw)
    except Exception as e:
        return {"error": str(e), "ioi_signals": [], "iod_signals": []}
