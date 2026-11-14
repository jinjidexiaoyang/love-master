"""
Replier — 回复生成器
输出三版本语义化回复：轻松版 / 稳妥版 / 推进版
"""
import json
from config import MODEL, MAX_TOKENS
from database import get_contact, get_chat_history, save_advisor_message, get_advisor_history
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
    return "\n".join(lines)


def _build_history_ctx(contact_name: str, limit: int = 12) -> str:
    history = get_chat_history(contact_name, limit=limit)
    if not history:
        return "（暂无历史记录）"
    lines = []
    for msg in history:
        role_label = "她" if msg["role"] == "her" else "我"
        lines.append(f"{role_label}：{msg['content']}")
    return "\n".join(lines)


def generate_reply(contact_name: str, her_message: str,
                   strategy_hint: str = "", technique_tags: list = None) -> dict:
    """
    生成三版本回复建议
    返回结构：
    {
      "analysis": "当前局面简析",
      "recommended_direction": "推荐方向",
      "reply_options": [
        {"label": "轻松版", "content": "...", "fit_reason": "...", "risk_level": "low"},
        {"label": "稳妥版", "content": "...", "fit_reason": "...", "risk_level": "low"},
        {"label": "推进版", "content": "...", "fit_reason": "...", "risk_level": "medium"},
      ],
      "avoid": ["不建议做的事1", "不建议做的事2"]
    }
    """
    engine = get_style_engine()
    contact_ctx = _contact_ctx(contact_name)

    # 取关系阶段注入对应 stage rules
    from database import get_contact as _gc
    _contact = _gc(contact_name)
    _stage_map = {
        "陌生人": "stranger", "认识": "acquaintance", "熟人": "familiar",
        "暧昧": "ambiguous", "上头": "intimate", "痴迷": "intimate",
    }
    _stage_key = _stage_map.get(
        _contact.get("stage", "陌生人") if _contact else "陌生人", "stranger")

    system_prompt = engine.inject(
        base_prompt="""你是一个聊天回复助手。严格基于上方开发者风格DNA生成回复。

【通用约束】
- 主回复必须自然、可直接发送；不允许出现语义断裂、乱码、奇怪拼接
- 主回复优先 1 句，最多 2 句；单句尽量 3-15 字
- 可追加最多 1 句（optional_followup），用于递进或留钩子，可为空字符串
- 不要长篇解释；不要心理咨询腔；不要自证；不要连续追问
- 不要为了像开发者而硬玩梗；口语化、微信风格、简洁有力
- advanced/restricted 技巧只在上方阶段规则允许时使用

【场景识别规则】
冷淡/低能量场景（对方回复：嗯/还行吧/随便/不知道/哦/忙/没事/好/行）：
  → 收住节奏，不追，不舔，不强行俏皮，给空间，保留轻微张力
  → 示例：「行」「那先这样」「懂了」「忙完找我」

试探/质疑场景（对方说：你是不是对很多人都这样/你是不是渣/你喜欢我吗）：
  → 不解释，不自证，反问或接梗，轻调侃
  → 绝对不要说"没有啊我只对你"这类低姿态话

正向兴趣场景（对方说：你挺有意思/你挺会/感觉你还不错）：
  → 接住，不谦虚，不舔，轻推一点

负面情绪场景（对方说：烦死了/累死了/崩了/不开心/烦/心情不好）：
  → 先轻接住，不立刻心理咨询，不做普通安慰腔，不马上乱调侃
  → 优先话术：「怎么了」「谁惹你了」「说说」「说说，我看看多离谱」「又谁惹你了」「展开讲讲」
  → 禁用心理咨询/安慰腔表达（见下方油腻禁区），单次出现也不行
  → 不要连续追问，不要滑向陪伴安抚腔；一句接住即可，不要变长
  → 看关系阶段决定是否推进，陌生人阶段保持轻接即可

轻松分享/日常吐槽场景（对方在分享生活、开玩笑、随口说一句）：
  → 顺着接，轻回应，不要立刻把它变成"欠我一个什么"或制造约定感
  → 不要把对方的随口一句话变成推进关系的筹码
  → 示例：对方说"喝多了哈哈哈" → 「喝多了还发消息」「注意点」，而不是「那欠我一个电话」
  → 推进版可以轻轻带一点，但不能算计感太强、太急

【油腻禁区】以下模式一律禁止：
  - "那你欠我一个xxx"（把对方的状态变成债务）
  - "那今天xxx了找我"（强行制造约定）
  - 任何让对方感觉被算计或被推进的句式
  - 过度关心追问（"怎么了""发生什么了""你还好吗"连续出现）
  - 心理咨询/安慰腔表达（单次出现也禁止）：
    · "深呼吸"、"放松点"、"慢慢来"、"慢慢说"、"别想太多"
    · "我理解你"、"我懂你"、"我一直在"
    · 任何"我陪你/我在这/让我来陪你"式陪伴安抚腔
  - 上述禁用表达在负面情绪场景下尤其不能出现，不是语气问题，是直接不用

只输出JSON，不要额外文字。""",
        technique_tags=technique_tags,
        include_cases=True,
        stage=_stage_key,
    )

    prompt = f"""【聊天对象档案】
{contact_ctx}

【近期聊天记录】
{_build_history_ctx(contact_name)}

【她最新发来的消息】
"{her_message}"

【策略方向】
{strategy_hint or "基于核心心态自然推进"}

请输出以下JSON格式：
{{
  "analysis": "当前局面简析（30字内）",
  "recommended_direction": "推荐方向（20字内）",
  "reply_options": [
    {{
      "label": "轻松版",
      "main_reply": "顺势接住，松弛自然，不推进不后退",
      "optional_followup": "可追加的第二句（可为空字符串）",
      "reason": "为什么这样回（15字内）",
      "risk_level": "low"
    }},
    {{
      "label": "稳妥版",
      "main_reply": "接住且有一点点存在感，不显急",
      "optional_followup": "可追加的第二句（可为空字符串）",
      "reason": "为什么这样回（15字内）",
      "risk_level": "low"
    }},
    {{
      "label": "推进版",
      "main_reply": "推进情感温度或张力，但不制造约定感、债务感、占便宜感；推进是让对方更想聊，不是让对方欠你什么",
      "optional_followup": "可追加的第二句（可为空字符串）",
      "reason": "为什么这样回（15字内）",
      "risk_level": "medium"
    }}
  ],
  "avoid": ["不建议做的事1", "不建议做的事2"]
}}"""

    try:
        response = client.messages.create(
            model=MODEL, max_tokens=800,
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
    except Exception as e:
        return {
            "analysis": "解析失败",
            "recommended_direction": "请重试",
            "reply_options": [{"label": "轻松版", "content": str(e), "fit_reason": "", "risk_level": "low"}],
            "avoid": []
        }


def generate_opening(contact_name: str, context: str = "",
                     technique_tags: list = None) -> dict:
    """生成开场白，同样返回三版本结构"""
    engine = get_style_engine()
    contact_ctx = _contact_ctx(contact_name)

    system_prompt = engine.inject(
        base_prompt="你是开场白生成专家。基于风格核心生成有记忆点的开场白。只输出JSON。",
        technique_tags=technique_tags,
        include_cases=True
    )

    prompt = f"""【聊天对象】
{contact_ctx}

【背景】{context or "无特殊背景"}

输出JSON：
{{
  "analysis": "当前适合什么风格的开场（20字内）",
  "recommended_direction": "推荐方向",
  "reply_options": [
    {{"label": "轻松版", "content": "开场白内容", "fit_reason": "适用原因", "risk_level": "low"}},
    {{"label": "稳妥版", "content": "开场白内容", "fit_reason": "适用原因", "risk_level": "low"}},
    {{"label": "推进版", "content": "开场白内容", "fit_reason": "适用原因", "risk_level": "medium"}}
  ],
  "avoid": ["不建议的开场方式"]
}}"""

    try:
        response = client.messages.create(
            model=MODEL, max_tokens=600,
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
    except Exception as e:
        return {"analysis": "", "recommended_direction": "", "reply_options": [], "avoid": []}


def evaluate_my_reply(contact_name: str, her_message: str, my_draft: str) -> dict:
    """
    评估用户想发的回复是否合适。不写入数据库。
    返回：{
      "verdict": "合适/勉强/不建议",
      "score": 1-10,
      "strengths": ["优点1"],
      "risks": ["风险1"],
      "suggestion": "如果要改，可以改成什么方向（1-2句）"
    }
    """
    engine = get_style_engine()
    contact_ctx = _contact_ctx(contact_name)
    history_ctx = _build_history_ctx(contact_name)

    from database import get_contact as _gc
    _contact = _gc(contact_name)
    _stage_map = {
        "陌生人": "stranger", "认识": "acquaintance", "熟人": "familiar",
        "暧昧": "ambiguous", "上头": "intimate", "痴迷": "intimate",
    }
    _stage_key = _stage_map.get(
        _contact.get("stage", "陌生人") if _contact else "陌生人", "stranger")

    system_prompt = engine.inject(
        base_prompt="""你是实战型回复评估顾问。基于开发者风格DNA，客观评估用户想发的回复是否符合当前局面和风格核心。只输出JSON，不要额外文字。""",
        include_cases=False,
        stage=_stage_key,
    )

    prompt = f"""【聊天对象档案】
{contact_ctx}

【近期聊天记录】
{history_ctx}

【她最新发来的消息】
"{her_message}"

【用户想发的回复（待评估）】
"{my_draft}"

请从以下维度客观评估这条回复，输出JSON：
{{
  "verdict": "合适/勉强/不建议",
  "score": 7,
  "strengths": ["符合的点1（≤20字）"],
  "risks": ["风险点1（≤20字）"],
  "suggestion": "如果要调整，方向是什么（1-2句，可给参考话术）"
}}

评估标准：
- 是否符合开发者风格DNA（松弛、不低姿态、不自证、不心理咨询腔）
- 是否适合当前关系阶段和局面
- 是否会推进还是损伤当前势能
- 语气是否自然可发"""

    try:
        response = client.messages.create(
            model=MODEL, max_tokens=600,
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
    except Exception as e:
        return {"verdict": "解析失败", "score": 0, "strengths": [], "risks": [str(e)], "suggestion": ""}


def advisor_chat(contact_name: str, user_question: str) -> str:
    engine = get_style_engine()
    contact_ctx = _contact_ctx(contact_name)
    history = get_advisor_history(contact_name)

    system_prompt = engine.inject(
        base_prompt=f"""你是顶级恋爱顾问，建议必须基于上方风格核心。
当前咨询对象：
{contact_ctx}

风格：直接、实用、像有经验的兄弟给建议，必要时给具体话术。""",
        include_cases=False
    )

    messages = [{"role": m["role"], "content": m["content"]} for m in history[-10:]]
    messages.append({"role": "user", "content": user_question})

    response = client.messages.create(
        model=MODEL, max_tokens=800,
        system=system_prompt,
        messages=messages
    )
    reply = response.content[0].text.strip()

    save_advisor_message(contact_name, "user", user_question)
    save_advisor_message(contact_name, "assistant", reply)
    return reply


def chat_tutor(contact_name: str, question: str,
               last_analysis: dict = None, tutor_history: list = None) -> str:
    """
    Tab1 答疑导师：基于历史聊天记录 + 最近分析结果回答用户问题。
    支持多轮对话（tutor_history 传入本次 session 内的对话历史）。
    不写入 advisor_history 表，session 内独立维护。
    """
    engine = get_style_engine()
    contact_ctx = _contact_ctx(contact_name)
    chat_history_ctx = _build_history_ctx(contact_name, limit=30)

    # 把最近分析结果整理成可读文本
    analysis_ctx = ""
    if last_analysis and "error" not in last_analysis:
        parts = []
        if last_analysis.get("analysis_summary"):
            parts.append(f"局面判断：{last_analysis['analysis_summary']}")
        if last_analysis.get("momentum"):
            momentum_map = {"rising": "升温", "stable": "平稳", "falling": "降温"}
            parts.append(f"对方状态：{momentum_map.get(last_analysis['momentum'], last_analysis['momentum'])}")
        if last_analysis.get("relationship_stage"):
            parts.append(f"关系阶段：{last_analysis['relationship_stage']}")
        if last_analysis.get("interest_score") is not None:
            parts.append(f"好感度：{last_analysis['interest_score']}/10")
        if last_analysis.get("ambiguity_percent") is not None:
            parts.append(f"暧昧指数：{last_analysis['ambiguity_percent']}%")
        if last_analysis.get("ioi_signals"):
            parts.append(f"正向信号：{'、'.join(last_analysis['ioi_signals'])}")
        if last_analysis.get("iod_signals"):
            parts.append(f"风险信号：{'、'.join(last_analysis['iod_signals'])}")
        if last_analysis.get("opportunities"):
            parts.append(f"机会点/行动建议：{'；'.join(last_analysis['opportunities'])}")
        if last_analysis.get("red_flags"):
            parts.append(f"红旗警告：{'；'.join(last_analysis['red_flags'])}")
        if last_analysis.get("recommended_techniques"):
            parts.append(f"适合技巧：{'；'.join(last_analysis['recommended_techniques'])}")
        if last_analysis.get("avoid_techniques"):
            avoid = last_analysis["avoid_techniques"]
            if avoid and isinstance(avoid[0], dict):
                avoid_strs = [f"{a.get('technique','')}（{a.get('reason','')}）" for a in avoid]
            else:
                avoid_strs = [str(a) for a in avoid]
            parts.append(f"不建议行为：{'；'.join(avoid_strs)}")
        analysis_ctx = "\n".join(parts)

    system_prompt = engine.inject(
        base_prompt=f"""你是实战型聊天导师，基于开发者风格DNA和当前聊天记录帮用户复盘、答疑、纠错。

【联系人档案】
{contact_ctx}

【近期聊天记录（最近20条）】
{chat_history_ctx}

{f"【最近一次AI分析结果】{chr(10)}{analysis_ctx}" if analysis_ctx else ""}

回答风格：
- 直接说结论，不绕弯
- 有问题就指出来，不护短
- 给具体建议或参考话术，不给泛泛方向
- 引用聊天记录时只能引用上方【近期聊天记录】中实际存在的句子，禁止编造或补全不存在的消息
- 如果聊天记录里没有相关内容，直接说"记录里没有这条消息"，不要猜测或捏造
- 不超过200字，除非问题需要详细拆解""",
        include_cases=False,
    )

    messages = []
    if tutor_history:
        for m in tutor_history[-8:]:
            messages.append({"role": m["role"], "content": m["content"]})
    messages.append({"role": "user", "content": question})

    response = client.messages.create(
        model=MODEL, max_tokens=800,
        system=system_prompt,
        messages=messages
    )
    return response.content[0].text.strip()
