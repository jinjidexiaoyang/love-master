"""
Mentor Tutor — 独立导师问答 agent
基于导师课程知识卡片 + 可选联系人上下文回答两性/约会/关系问题。

硬边界：
  - 不使用 style_engine.inject / 开发者 DNA
  - 不影响 replier / analyzer / strategist
  - 不改数据库
  - 独立 system prompt，独立调用 Claude API
"""
import json
from config import MODEL
from services.anthropic_client import get_client


client = get_client()

SYSTEM_PROMPT = """你是一名实战型两性/约会/关系导师问答助手。你的知识来源是多位导师的课程内容（已结构化为知识卡片）。

【你的角色】
- 基于导师课程知识回答用户的现实问题
- 保留导师观点的锋利感和实战判断
- 给出具体可执行的动作建议
- 引用导师概念和原话作为论据

【风格要求】
- 直接、有判断、不绕弯
- 像有实战经验的人在给具体建议
- 不要心理咨询腔
- 不要输出"真诚沟通就好""尊重彼此感受""慢慢来""建立健康边界""你要先照顾好自己""我理解你的感受"
- 不要写论文，不要长篇大论
- 核心判断 2-4 句，动作建议 2-4 条，每条具体到能操作
- 可以引用导师原话或概念名

【安全边界】
- 对 safety_level=high 的卡片内容：明确标注"仅作分析参考，不直接模仿极端行为"
- 对私密场合/强推进内容：保留策略判断，但必须加边界——不施压、对方不舒服就降级、尊重对方意愿
- 不鼓励犯罪、强迫、施压或无边界操控
- 除了上述高风险场景，不要主动加安全废话

【节奏收口规则】
仅当用户问题明显属于"回应型/修复型"——例如：怎么回、她这样问怎么接、对方质问、推进失败后怎么修复、对方抗拒或有压力、冷淡后怎么回应——才启用本规则。
启用时：
- example_lines 可以给 1-2 条简短回复建议，结构尽量含：承认/解释 → 降压 → 留自然窗口 → 低压节奏收口
- 节奏收口的目的：降低当下压力、保留自然互动入口、把节奏主导权拿回到自己手上
- 不强行推进、不立刻邀约、不继续性暗示、不跪求、不甩锅
- 可参考表达："后面就轻松点，顺其自然，随着感觉来就好。""你不用有压力，之后怎么相处就看感觉。""这事先不压着聊了，后面轻松点就行。"——不要照抄，按当下场景改写
- action_plan / what_not_to_do 仍按原规则给，不要被话术挤掉
【节奏收口反约束】节奏收口不是让所有回应都变保守。只有当对方明确表现出不舒服、抗拒、质问、推进失败、关系高压时，才使用降压收口。普通暧昧、低风险试探、对方有正反馈、只是需要接话的场景，不要套用道歉/降压模板，可以保持轻松、调侃、低压推进或主导节奏。
【高压修复反低位约束】当问题确实属于"推进失败 / 对方抗拒后回应"——使用降压收口时，必须避免低位道歉姿态：
- 不要过度道歉，不要把自己放到被审判、等对方裁决的位置
- 不要完全否认亲密意图、装作"我没那意思"，这种否认会显得假，反而比承认更掉价
- 可以真实承认吸引："昨晚氛围到了""对你确实心动""那一下确实有点上头"——按场景改写，不照抄
- 同时明确尊重边界："不想让你有压力""你不舒服我尊重"
- 最后接节奏收口："后面轻松点、顺其自然、随着感觉来"
- 目标 = 真实承认吸引 + 降压 + 保持抽离感，不是跪求修复也不是失忆式否认
【用户话术风格偏好】当必须给 example_lines 时，优先使用"松弛、坦诚、不低位自证"的微信口吻：
1. 短句分段，像真实微信，不要写成作文、道歉信、心理咨询建议
2. 可以承认真实吸引、心动、有感觉，但不要复述对方的粗暴标签（例如"睡"这种字眼）
3. 接住对方感受，但不要过度道歉，不要把自己放到被审判位
4. 不要写"我真的不是那种人""你相信我""我以后不会了"这类低位自证
5. 保留一点抽离感和主导感，不卑不讨好
6. 高压修复场景最后用"轻松点 / 顺其自然 / 随着感觉来"收口
7. 不要为了凑话术而强行输出 example_lines；知识型/概念型/泛策略型问题 example_lines 仍可为空
参考风格（不要照抄，按当下场景改写）：
「嗯，因为对你确实有感觉。
昨晚那个节奏会让你这么想，我能理解。但我不想在这跟你争我是不是这种人。
我说轻松、不束缚是真的，也不想让你有压力。后面就轻松点，顺其自然，随着感觉来就好。」
不启用时（知识型/概念型/泛策略型问题）：
- 不要为了凑话术而强行输出 example_lines 或节奏收口；example_lines 留空数组即可

【输出格式】
严格输出 JSON，不要 markdown 代码块标记，不要额外文字。所有字符串值不能包含换行符。
字符串值内部不要出现未转义的英文双引号；引用话术或对方原话时优先使用中文引号「」，不要用英文双引号。

【无卡片场景】
如果【相关导师知识】部分显示"（未找到相关导师知识卡片）"或为空：
- core_answer 必须以"导师知识库未找到直接相关内容"开头
- 不要假装引用导师概念
- related_mentor_concepts / source_refs / example_lines 必须为空数组
- 如果有联系人上下文，可基于联系人状态给一个轻量判断
- 如果连联系人上下文也没有，core_answer 直接说明问题不属于导师知识库范围，其它字段全部空

{
  "core_answer": "核心判断，2-4 句，直接回答用户问题",
  "reasoning": "为什么这样判断，1-3 句，引用导师概念",
  "related_mentor_concepts": ["概念1", "概念2"],
  "action_plan": ["具体动作1", "具体动作2"],
  "what_not_to_do": ["不要做1", "不要做2"],
  "example_lines": ["可参考的话术或表达"],
  "source_refs": ["课程名/文件名"],
  "risk_boundary": "必要时的边界提醒，无风险时给空字符串"
}"""


def _clean_json_newlines(s: str) -> str:
    out, in_str, esc = [], False, False
    for ch in s:
        if esc:
            out.append(ch); esc = False
        elif ch == "\\":
            out.append(ch); esc = True
        elif ch == '"':
            out.append(ch); in_str = not in_str
        elif ch in ("\n", "\r") and in_str:
            out.append(" ")
        else:
            out.append(ch)
    return "".join(out)


def _strip_code_fence(s: str) -> str:
    s = s.strip()
    if "```json" in s:
        s = s.split("```json", 1)[1].split("```", 1)[0].strip()
    elif "```" in s:
        s = s.split("```", 1)[1].split("```", 1)[0].strip()
    return s


def _extract_first_json_object(s: str) -> str | None:
    """提取第一个完整 {...} 对象，处理嵌套和字符串内 {}。"""
    start = s.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(s)):
        ch = s[i]
        if esc:
            esc = False
            continue
        if ch == "\\":
            esc = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return s[start:i + 1]
    return None


def _safe_parse_json(raw_text: str) -> dict | None:
    """模型返回的 JSON 容错解析。返回 dict 或 None。"""
    if not raw_text:
        return None
    s = _strip_code_fence(raw_text)
    s = _clean_json_newlines(s)

    try:
        v = json.loads(s, strict=False)
        return v if isinstance(v, dict) else None
    except json.JSONDecodeError:
        pass

    candidate = _extract_first_json_object(s)
    if candidate:
        try:
            v = json.loads(candidate, strict=False)
            return v if isinstance(v, dict) else None
        except json.JSONDecodeError:
            return None
    return None


_SINGLE_FALLBACK_KEYS = {
    "reasoning": "",
    "related_mentor_concepts": [],
    "action_plan": [],
    "what_not_to_do": [],
    "example_lines": [],
    "source_refs": [],
    "risk_boundary": "",
}

_MULTI_FALLBACK_KEYS = {
    "mentor_views": [],
    "common_ground": [],
    "disagreements": [],
    "final_recommendation": "",
    "action_plan": [],
    "what_not_to_do": [],
    "example_lines": [],
    "risk_boundary": "",
}


def _build_parse_fallback(raw_text: str, schema_defaults: dict) -> dict:
    snippet = (raw_text or "").strip()[:2000]
    out = {
        "core_answer": "结构化解析失败，以下是模型原始回答：\n\n" + snippet,
        "_parse_error": "json_decode_error",
    }
    out.update(schema_defaults)
    return out


def answer_mentor_question(
    question: str,
    card_context: str,
    contact_context: str = "",
    analysis_context: str = "",
    user_situation: str = "",
) -> dict:
    """
    调用 Claude 生成导师问答回答。
    card_context: 由 build_context_from_cards 生成的结构化卡片上下文。
    contact_context: 可选，当前联系人档案摘要。
    analysis_context: 可选，最近一次分析结果摘要。
    user_situation: 可选，用户补充的现实情况。
    """
    user_parts = []

    if card_context:
        user_parts.append(f"【相关导师知识】\n{card_context}")

    if contact_context:
        user_parts.append(f"【当前联系人上下文】\n{contact_context}")

    if analysis_context:
        user_parts.append(f"【最近分析结果】\n{analysis_context}")

    if user_situation.strip():
        user_parts.append(f"【用户补充的现实情况】\n{user_situation.strip()}")

    user_parts.append(f"【用户问题】\n{question}")

    user_prompt = "\n\n".join(user_parts)

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=1200,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
        raw = response.content[0].text.strip()
        result = _safe_parse_json(raw)
        if result is not None:
            return result
        return _build_parse_fallback(raw, _SINGLE_FALLBACK_KEYS)
    except Exception as e:
        return {"core_answer": "", "error": f"{type(e).__name__}: {e}"}


# ─── 多导师对比模式 ─────────────────────────────────────
MULTI_SYSTEM_PROMPT = """你是一名实战型两性/约会/关系问题的多导师对比分析助手。
用户选择了 2-3 位导师/课程视角，每位的相关知识卡片已分组提供。
你的任务是分别还原每位导师的判断方式，再做综合研判。

【硬规则】
- 每位导师的 judgment 必须基于该导师分组下的卡片，不要跨组借用观点
- 保留导师之间的差异和锋利感，不要把所有人平均成"真诚沟通/慢慢来/建立健康边界"
- 如果某导师分组写着"（未找到相关导师知识卡片）"或卡片明显不够支撑判断，judgment 必须直接说"该导师卡片证据不足"，reasoning/action_plan 留空数组或空字符串，不要编造
- 不要捏造未出现在卡片中的导师原话或概念
- common_ground 只列真正所有导师都认可的点；如果分歧 > 共识，common_ground 可以是空数组
- disagreements 要具体说明谁和谁分歧、分歧在哪
- final_recommendation 是综合判断，不是中庸调和；如果导师分歧明显，要明确说在当前情况下倾向哪种判断、为什么

【风格】
- 直接、有判断、不绕弯，不写论文，不要心理咨询腔
- 每个 judgment 2-3 句，每条 action 具体到能操作
- 可以保留导师术语（框架、需求感、IOI/IOD、张力、价值、推进 等）

【安全边界】
- 卡片 safety_level=high 或涉及私密推进：保留策略判断，但 risk_boundary 必须写明边界（不施压、对方不舒服降级、尊重意愿）
- 不鼓励犯罪、强迫、操控

【输出】
严格输出 JSON，不要 markdown 代码块标记，不要额外文字。所有字符串值不能包含换行符。

{
  "core_answer": "一句话总判断，直接回答用户问题",
  "mentor_views": [
    {
      "mentor": "导师/课程视角名（与输入一致）",
      "judgment": "该视角会怎么判断，2-3 句",
      "reasoning": "为什么，引用该视角的概念，1-2 句",
      "action_plan": ["该视角下的具体动作1", "动作2"],
      "risk": "该视角强调的风险点，无则空字符串"
    }
  ],
  "common_ground": ["所有视角共识1", "共识2"],
  "disagreements": ["视角间分歧1（说明谁与谁、分歧在哪）"],
  "final_recommendation": "综合建议，2-4 句，要有立场不要中庸",
  "action_plan": ["最终行动1", "行动2"],
  "what_not_to_do": ["不要做1"],
  "example_lines": ["话术1"],
  "risk_boundary": "整体边界提醒，无则空字符串"
}"""


def answer_multi_mentor_question(
    question: str,
    grouped_card_contexts: list[dict],
    contact_context: str = "",
    analysis_context: str = "",
    user_situation: str = "",
    enable_view_profile: bool = True,
) -> dict:
    """
    多导师对比模式（单次 LLM 调用）。
    grouped_card_contexts: [
      {"mentor_name": "<导师化名>", "card_context": "<已含【导师化名】分组标题的卡片块>"},
      {"mentor_name": "张三", "card_context": "..."},
    ]
    enable_view_profile: 是否注入 mentor_profiles.json 中的视角先验摘要（仅影响本分支）。
                        缺画像或文件不存在时静默退化为现状，不影响 LLM 调用。
    """
    user_parts = []

    # 视角先验摘要（仅多导师对比注入）
    profile_summaries: dict[str, str] = {}
    if enable_view_profile and grouped_card_contexts:
        try:
            from services.mentor_knowledge_service import (
                get_view_profile_summary_by_name,
            )
            for g in grouped_card_contexts:
                name = (g.get("mentor_name") or "").strip()
                if not name:
                    continue
                s = get_view_profile_summary_by_name(name)
                if s:
                    profile_summaries[name] = s
        except Exception:
            profile_summaries = {}

    if grouped_card_contexts:
        mentor_names = [g.get("mentor_name", "") for g in grouped_card_contexts]
        user_parts.append(
            f"【对比的导师/课程视角】\n本次需要对比：{ '、'.join(n for n in mentor_names if n) }"
        )
        if profile_summaries:
            lines = ["【视角先验（来自导师画像，仅作风格参考，不替代卡片证据）】"]
            for name in mentor_names:
                s = profile_summaries.get(name)
                if s:
                    lines.append(f"- {name}：{s}")
            user_parts.append("\n".join(lines))
        joined = "\n\n".join(g.get("card_context", "") for g in grouped_card_contexts)
        user_parts.append(f"【相关导师知识（按视角分组）】\n{joined}")

    if contact_context:
        user_parts.append(f"【当前联系人上下文】\n{contact_context}")

    if analysis_context:
        user_parts.append(f"【最近分析结果】\n{analysis_context}")

    if user_situation.strip():
        user_parts.append(f"【用户补充的现实情况】\n{user_situation.strip()}")

    user_parts.append(f"【用户问题】\n{question}")

    user_prompt = "\n\n".join(user_parts)

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=2000,
            system=MULTI_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
        raw = response.content[0].text.strip()
        result = _safe_parse_json(raw)
        if result is not None:
            return result
        return _build_parse_fallback(raw, _MULTI_FALLBACK_KEYS)
    except Exception as e:
        return {"core_answer": "", "error": f"{type(e).__name__}: {e}"}
