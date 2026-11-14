"""
utils/daily_scoring.py — 按天从聊天记录本地计算好感度/暧昧度，无需调用 LLM
"""
import re
import sqlite3
from collections import defaultdict
from datetime import datetime

from config import DB_PATH
from utils.scoring import compute_interest_score, compute_ambiguity_percent
from database import save_metrics

IOI_KEYWORDS = ["主动", "哈哈", "嘻嘻", "想你", "好想", "撒娇", "哇", "好棒",
                "喜欢", "爱你", "❤", "😊", "😍", "🥰", "亲", "宝", "开心", "高兴"]
IOD_KEYWORDS = ["哦", "嗯", "忙", "随便", "不知道", "算了", "无所谓",
                "好吧", "呵", "懒得", "烦", "不想"]
AMBIGUITY_KEYWORDS = {
    "flirty_tone":          ["撩", "暧昧", "坏", "色色"],
    "playful_banter":       ["哈哈", "嘻嘻", "笑死", "哈哈哈"],
    "uses_nickname":        ["宝", "亲", "傻瓜", "笨蛋", "小"],
    "shares_private":       ["秘密", "只告诉你", "悄悄", "不告诉别人"],
    "daily_checkin":        ["早", "晚安", "睡了", "起床", "吃了吗"],
    "offline_intent":       ["见面", "约", "出来", "一起", "来找我", "去哪"],
    "emotional_dependency": ["想你", "好想", "没你", "陪我", "想见"],
}
_EMOJI_RE = re.compile(r'[\U0001F300-\U0001FFFF]', flags=re.UNICODE)


def _median_reply_gap(msgs: list) -> str:
    """计算她发消息后我回复的中位时间差，返回 fast/normal/slow"""
    gaps = []
    for i, m in enumerate(msgs):
        if m["role"] != "her":
            continue
        for j in range(i + 1, len(msgs)):
            if msgs[j]["role"] == "me":
                try:
                    t1 = datetime.strptime(m["timestamp"], "%Y-%m-%d %H:%M:%S")
                    t2 = datetime.strptime(msgs[j]["timestamp"], "%Y-%m-%d %H:%M:%S")
                    diff = (t2 - t1).total_seconds() / 60
                    if 0 <= diff <= 240:
                        gaps.append(diff)
                except Exception:
                    pass
                break
    if not gaps:
        return "normal"
    median = sorted(gaps)[len(gaps) // 2]
    if median < 5:
        return "fast"
    if median > 60:
        return "slow"
    return "normal"


def compute_daily_scores(contact_name: str) -> list:
    """
    从 chat_history 按天分组，纯规则计算每天的好感度和暧昧度。
    返回 [{"day": "2026-04-13", "interest_score": 3.5, "ambiguity_percent": 20}, ...]
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT substr(timestamp,1,10) as day, role, content, timestamp "
        "FROM chat_history WHERE contact_name=? ORDER BY timestamp ASC",
        (contact_name,)
    ).fetchall()
    conn.close()

    days = defaultdict(list)
    for r in rows:
        days[r["day"]].append(dict(r))

    results = []
    for day in sorted(days.keys()):
        msgs = days[day]
        her = [m for m in msgs if m["role"] == "her"]
        me  = [m for m in msgs if m["role"] == "me"]

        if not her:
            continue

        avg_len = sum(len(m["content"]) for m in her) / len(her)
        ioi_hits = sum(1 for m in her for kw in IOI_KEYWORDS if kw in m["content"])
        iod_hits = sum(1 for m in her for kw in IOD_KEYWORDS if kw in m["content"])
        emoji_count = sum(len(_EMOJI_RE.findall(m["content"])) for m in her)
        ratio = len(her) / max(len(me), 1)

        interest_signals = {
            "reply_speed":      _median_reply_gap(msgs),
            "reply_length":     "long" if avg_len > 30 else ("short" if avg_len < 8 else "medium"),
            "initiated_topics": 2 if ratio >= 1.5 else (1 if ratio >= 1.0 else 0),
            "questions_asked":  sum(1 for m in her if "?" in m["content"] or "？" in m["content"]),
            "shared_personal":  ioi_hits >= 2,
            "positive_emotion": emoji_count > 0 or ioi_hits > 0,
            "showed_coldness":  iod_hits >= 2,
        }

        ambiguity_signals = {
            key: any(kw in m["content"] for m in her for kw in kws)
            for key, kws in AMBIGUITY_KEYWORDS.items()
        }

        interest_score, _ = compute_interest_score(interest_signals)
        ambiguity_percent, _ = compute_ambiguity_percent(ambiguity_signals)

        results.append({
            "day": day,
            "interest_score": interest_score,
            "ambiguity_percent": ambiguity_percent,
        })

    return results


def rebuild_daily_metrics(contact_name: str) -> int:
    """
    清空该联系人的 metrics_history，重新按天计算并写入。
    返回写入的天数。
    """
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM metrics_history WHERE contact_name=?", (contact_name,))
    conn.commit()
    conn.close()

    daily = compute_daily_scores(contact_name)
    for d in daily:
        save_metrics(contact_name, d["ambiguity_percent"], d["interest_score"],
                     stage="", timestamp=d["day"])
    return len(daily)
