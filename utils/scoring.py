"""可解释规则评分模型"""
import json


def compute_interest_score(signals: dict) -> tuple[float, list]:
    """
    基于可解释规则计算兴趣评分 0-10
    signals 字段：
      reply_speed: fast/normal/slow
      reply_length: long/medium/short
      initiated_topics: int
      questions_asked: int
      shared_personal: bool
      positive_emotion: bool
      accepted_invite: bool/None
      showed_coldness: bool
    返回 (score, reasons)
    """
    score = 5.0
    reasons = []

    speed = signals.get("reply_speed", "normal")
    if speed == "fast":
        score += 1.0
        reasons.append("回复速度快（+1）")
    elif speed == "slow":
        score -= 1.0
        reasons.append("回复速度慢（-1）")

    length = signals.get("reply_length", "medium")
    if length == "long":
        score += 0.5
        reasons.append("回复内容详细（+0.5）")
    elif length == "short":
        score -= 0.5
        reasons.append("回复内容简短（-0.5）")

    initiated = signals.get("initiated_topics", 0)
    if initiated >= 2:
        score += 1.0
        reasons.append(f"主动开启话题{initiated}次（+1）")
    elif initiated == 1:
        score += 0.5
        reasons.append("主动开启话题1次（+0.5）")

    questions = signals.get("questions_asked", 0)
    if questions >= 2:
        score += 0.5
        reasons.append(f"主动提问{questions}次（+0.5）")

    if signals.get("shared_personal"):
        score += 0.5
        reasons.append("分享了私人信息（+0.5）")

    if signals.get("positive_emotion"):
        score += 0.5
        reasons.append("情绪积极（+0.5）")

    accepted = signals.get("accepted_invite")
    if accepted is True:
        score += 1.5
        reasons.append("接受了邀约（+1.5）")
    elif accepted is False:
        score -= 1.5
        reasons.append("拒绝了邀约（-1.5）")

    if signals.get("showed_coldness"):
        score -= 1.5
        reasons.append("出现冷淡/敷衍信号（-1.5）")

    score = round(max(0.0, min(10.0, score)), 1)
    return score, reasons


def compute_ambiguity_percent(signals: dict) -> tuple[int, list]:
    """
    基于可解释规则计算暧昧指数 0-100
    signals 字段：
      flirty_tone: bool
      playful_banter: bool
      uses_nickname: bool
      shares_private: bool
      daily_checkin: bool
      offline_intent: bool
      emotional_dependency: bool
    返回 (percent, reasons)
    """
    score = 0
    reasons = []

    weights = [
        ("flirty_tone", 20, "暧昧语气"),
        ("playful_banter", 15, "调侃互动"),
        ("uses_nickname", 10, "使用昵称"),
        ("shares_private", 15, "分享私密内容"),
        ("daily_checkin", 10, "日常报备倾向"),
        ("offline_intent", 20, "线下互动意愿"),
        ("emotional_dependency", 10, "情绪依赖倾向"),
    ]

    for key, weight, label in weights:
        if signals.get(key):
            score += weight
            reasons.append(f"{label}（+{weight}）")

    score = min(100, score)
    return score, reasons


def score_from_analysis(analysis_result: dict) -> dict:
    """从 AI 分析结果中提取信号并计算评分，附加可解释说明"""
    ioi = analysis_result.get("ioi_signals", [])
    iod = analysis_result.get("iod_signals", [])

    interest_signals = {
        "reply_speed": "fast" if any("快" in s or "秒回" in s for s in ioi) else
                       "slow" if any("慢" in s or "很久" in s for s in iod) else "normal",
        "reply_length": "long" if any("详细" in s or "长" in s for s in ioi) else
                        "short" if any("简短" in s or "单字" in s for s in iod) else "medium",
        "initiated_topics": sum(1 for s in ioi if "主动" in s or "开启" in s),
        "questions_asked": sum(1 for s in ioi if "问" in s or "提问" in s),
        "shared_personal": any("私" in s or "分享" in s for s in ioi),
        "positive_emotion": any("积极" in s or "开心" in s or "撒娇" in s for s in ioi),
        "showed_coldness": any("冷淡" in s or "敷衍" in s or "单字" in s for s in iod),
    }

    ambiguity_signals = {
        "flirty_tone": any("暧昧" in s or "撩" in s for s in ioi),
        "playful_banter": any("调侃" in s or "玩笑" in s for s in ioi),
        "uses_nickname": any("昵称" in s or "小名" in s for s in ioi),
        "shares_private": any("私密" in s or "秘密" in s for s in ioi),
        "daily_checkin": any("报备" in s or "日常" in s for s in ioi),
        "offline_intent": any("见面" in s or "线下" in s or "约" in s for s in ioi),
        "emotional_dependency": any("依赖" in s or "情绪" in s for s in ioi),
    }

    interest_score, interest_reasons = compute_interest_score(interest_signals)
    ambiguity_percent, ambiguity_reasons = compute_ambiguity_percent(ambiguity_signals)

    # 如果 AI 已给出评分，做加权融合（AI 60% + 规则 40%）
    ai_interest = analysis_result.get("interest_score")
    ai_ambiguity = analysis_result.get("ambiguity_percent")

    if ai_interest is not None:
        interest_score = round(float(ai_interest) * 0.6 + interest_score * 0.4, 1)
    if ai_ambiguity is not None:
        ambiguity_percent = int(int(ai_ambiguity) * 0.6 + ambiguity_percent * 0.4)

    return {
        "interest_score": interest_score,
        "interest_reasons": interest_reasons,
        "ambiguity_percent": ambiguity_percent,
        "ambiguity_reasons": ambiguity_reasons,
        "disclaimer": "以上指标为辅助参考，不代表客观事实。"
    }
