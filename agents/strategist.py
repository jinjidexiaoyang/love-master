"""
Strategist — 策略生成器
基于联系人状态生成当前最优博弈策略
"""
import json
from config import MODEL, MAX_TOKENS, GAME_TECHNIQUES
from database import get_contact, get_chat_history, get_cases
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


def _contact_ctx(contact_name: str) -> str:
    contact = get_contact(contact_name)
    if not contact:
        return f"新联系人：{contact_name}"
    lines = [
        f"联系人：{contact_name}",
        f"关系阶段：{contact.get('stage', '陌生人')}",
        f"好感度：{contact.get('interest_score', 0)}/10",
        f"暧昧指数：{contact.get('ambiguity_percent', 0)}%",
        f"势头：{contact.get('momentum', 'stable')}",
    ]
    if contact.get("last_analysis"):
        lines.append(f"最近分析：{contact['last_analysis']}")
    for field, label in [("personality_tags", "性格"), ("interests", "兴趣")]:
        val = contact.get(field, "[]")
        try:
            items = json.loads(val) if isinstance(val, str) else val
            if items:
                lines.append(f"{label}：{', '.join(items)}")
        except Exception:
            pass
    return "\n".join(lines)


def generate_strategy(contact_name: str, current_context: str = "") -> dict:
    engine = get_style_engine()
    contact_ctx = _contact_ctx(contact_name)

    # 取关系阶段注入对应 stage rules
    contact = get_contact(contact_name)
    _stage_map = {
        "陌生人": "stranger", "认识": "acquaintance", "熟人": "familiar",
        "暧昧": "ambiguous", "上头": "intimate", "痴迷": "intimate",
    }
    stage_key = _stage_map.get(
        contact.get("stage", "陌生人") if contact else "陌生人", "stranger")

    system_prompt = engine.inject(
        base_prompt="你是实战型关系策略顾问。基于风格DNA和当前局面给出战术报告。要求：专业但可执行，有判断有动作，不要泛泛建议，不要心理咨询腔。只返回JSON，不要额外文字。",
        include_cases=True,
        stage=stage_key,
    )

    prompt = f"""【联系人档案】
{contact_ctx}

【当前背景】
{current_context or "基于现有档案给出策略"}

【可用技巧库】
{', '.join(GAME_TECHNIQUES)}

输出JSON：
{{
  "current_situation_assessment": "当前局面关键矛盾（2-3句，说清楚她在做什么、她想测试什么、核心博弈点在哪）",
  "priority_techniques": [
    {{
      "technique": "技巧名",
      "why": "为什么现在适合用（结合当前局面，1句话）",
      "how": "具体怎么用，给一个可直接参考的话术或动作",
      "condition": "在什么条件下才用（避免滥用）"
    }}
  ],
  "avoid_techniques": [
    {{
      "technique": "技巧名或行为",
      "reason": "现在用会有什么后果（1句话，要具体）"
    }}
  ],
  "tone_style": "当前合适的语气风格（2-4个词）",
  "pacing_advice": "节奏控制要点，3条以内，每条1句，可执行",
  "next_milestone": "下一个具体可见的目标（1句话，要可验证）",
  "key_move": "当前最关键一招：给出可直接用的动作或参考话术，不要只写方向",
  "risk_warning": "具体风险：说明如果做了某件事会怎样，不要写抽象提醒"
}}"""

    try:
        response = client.messages.create(
            model=MODEL, max_tokens=MAX_TOKENS,
            system=system_prompt,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = response.content[0].text.strip()
        if "```json" in raw:
            raw = raw.split("```json")[1].split("```")[0].strip()
        elif "```" in raw:
            raw = raw.split("```")[1].split("```")[0].strip()
        raw = _clean_json_newlines(raw)
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"key_move": raw[:300], "error": "JSON解析失败"}
    except Exception as e:
        return {"error": str(e)}


def get_technique_guide(technique_name: str, contact_name: str) -> str:
    engine = get_style_engine()
    contact_ctx = _contact_ctx(contact_name)

    system_prompt = engine.inject(
        base_prompt="你是技巧讲解专家。基于风格DNA解释技巧的使用方法。",
        include_cases=False
    )

    prompt = f"""【聊天对象】
{contact_ctx}

详细说明如何对这个女生使用「{technique_name}」：
1. **原理**：心理学原理
2. **时机**：什么时候用/不能用
3. **具体操作**：针对她性格的3个执行示例
4. **注意事项**：使用时注意什么
5. **效果判断**：什么反应说明奏效了"""

    response = client.messages.create(
        model=MODEL, max_tokens=800,
        system=system_prompt,
        messages=[{"role": "user", "content": prompt}]
    )
    return response.content[0].text.strip()
