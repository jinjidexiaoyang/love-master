"""
utils/language_features.py — 轻量聊天统计，纯规则，不调 LLM，不落库。

对外只暴露一个函数：compute_chat_stats(contact_name) -> dict | None
"""
import re
import sqlite3
from collections import Counter

from config import DB_PATH

# ── 停用词（低信息量，过滤掉不计入高频词） ──────────────────────────────────
_STOPWORDS = {
    "的", "了", "是", "在", "我", "你", "他", "她", "它", "们",
    "这", "那", "就", "都", "和", "与", "但", "也", "很", "有",
    "没", "不", "一", "个", "上", "对", "说", "好", "要", "么",
    "啊", "呢", "吧", "哦", "嗯", "然后", "所以", "因为", "如果",
    "可以", "还是", "已经", "什么", "怎么", "为什么", "就是", "还有",
    "其实", "感觉", "现在", "时候", "一个", "这个", "那个", "一下",
    "一起", "一直", "一样", "一点", "一些", "哈哈", "哈哈哈",
    "嗯嗯", "好的", "ok", "OK", "哎", "呀", "噢",
    # 补充高频无意义短语
    "不是", "没有", "还是", "就是", "不知", "知道", "不了", "不好",
    "吗吗", "哦哦", "嗯嗯", "哈哈", "呢呢", "啊啊", "吧吧",
    "一直", "一起", "一样", "一下", "一点", "一些", "一天",
    "然后", "所以", "因为", "如果", "其实", "感觉", "觉得",
    "这样", "那样", "这么", "那么", "怎么", "什么", "为什么",
    "没事", "没关系", "好像", "应该", "可能", "或者", "还有",
    # 微信/聊天元信息词
    "引用", "回复", "转发", "撤回", "拍一拍", "图片", "视频",
    "语音", "表情", "链接", "文件", "位置", "小程序", "聊天",
    "聊天记录", "公众号", "红包", "转账", "名片", "系统消息",
}

# ── 低信号消息正则（过滤后不参与特征统计） ────────────────────────────────
_NOISE_RE = re.compile(
    r"^(好|嗯+|哦|哈+|ok|OK|好的|收到|嗯嗯+|好哒|好滴|是的|对|呢|吧|啊|哎|\[.*?\])$",
    re.IGNORECASE,
)
_EMOJI_ONLY_RE = re.compile(r"^[\U0001F300-\U0001FFFF\U00002600-\U000027BF\s]+$")
_XML_RE = re.compile(r"^\s*<")
_URL_RE = re.compile(r"https?://")
_MULTILINE_RE = re.compile(r"\n.*\n")
_SHARE_RE = re.compile(
    r"^(【|通过网盘分享|【京东】|【淘宝】|【拼多多】|链接:|提取码:)"
)
_EMOJI_TOKEN_RE = re.compile(r"[\U0001F300-\U0001FFFF\U00002600-\U000027BF]+")

# ── 情绪词表 ───────────────────────────────────────────────────────────────
_POSITIVE_WORDS = [
    "开心", "高兴", "快乐", "喜欢", "爱", "棒", "不错", "满意",
    "幸福", "兴奋", "好玩", "哈哈", "嘻嘻", "好耶", "太好了",
    "感谢", "谢谢", "厉害", "牛", "赞",
]
_NEGATIVE_WORDS = [
    "难过", "伤心", "生气", "烦", "焦虑", "担心", "害怕",
    "失望", "委屈", "无聊", "讨厌", "崩溃", "郁闷", "心累",
    "烦死了", "算了", "随便", "无所谓",
]

# ── 观点/规划词（可选，暂不对外输出，留作扩展） ────────────────────────────
_OPINION_WORDS = ["我觉得", "我认为", "感觉", "我想", "我希望", "在我看来", "我以为"]
_PLANNING_WORDS = ["打算", "计划", "准备", "决定", "目标", "将来", "以后", "未来"]

_MIN_MESSAGES = 20  # 少于此数视为"样本不足"


def _is_noise(text: str) -> bool:
    t = text.strip()
    if not t:
        return True
    if _NOISE_RE.fullmatch(t):
        return True
    if _EMOJI_ONLY_RE.match(t):
        return True
    if _XML_RE.match(t):
        return True
    if _URL_RE.search(t):
        return True
    if _MULTILINE_RE.search(t):
        return True
    if _SHARE_RE.match(t):
        return True
    return False


def _rate(texts: list[str], words: list[str]) -> float:
    """words 中任意词命中的消息比例（%）"""
    if not texts:
        return 0.0
    hits = sum(1 for t in texts if any(w in t for w in words))
    return round(hits / len(texts) * 100, 1)


_HANZI_RE = re.compile(r"[一-鿿]")
_ALL_HANZI_RE = re.compile(r"^[一-鿿]{2,4}$")
# 连续汉字段落切分（去掉非汉字字符后取连续汉字块）
_HANZI_SPAN_RE = re.compile(r"[一-鿿]+")


def _top_words(texts: list[str], n: int = 10) -> list[tuple[str, int]]:
    """
    从去噪消息中提取纯中文 n-gram（2-4 字）词频。

    过滤规则：
    - 候选词必须全部由汉字组成（无英文、数字、标点、拼音）
    - 长度 2-4 字
    - 不在停用词表中
    - 只在连续汉字块内部滑窗，不跨越非汉字边界
    """
    counter: Counter = Counter()
    for text in texts:
        # 先去 emoji，再找所有连续汉字块，在块内做滑窗
        clean = _EMOJI_TOKEN_RE.sub("", text)
        for span in _HANZI_SPAN_RE.finditer(clean):
            seg = span.group()
            for length in (2, 3, 4):
                for i in range(len(seg) - length + 1):
                    word = seg[i : i + length]
                    if word not in _STOPWORDS:
                        counter[word] += 1
    return counter.most_common(n)


def compute_chat_stats(contact_name: str) -> dict | None:
    """
    从 chat_history 读取全量记录，计算轻量统计指标。
    返回 dict，或 None（数据库异常）。

    返回结构：
    {
      "total_her": int,
      "total_me": int,
      "sufficient": bool,          # False 时其余字段可能为空
      "avg_len_her": float,
      "avg_len_me": float,
      "question_rate_her": float,  # %
      "positive_rate_her": float,  # %
      "negative_rate_her": float,  # %
      "hourly_her": list[int],     # 长度 24，各小时消息数
      "hourly_me": list[int],
      "top_words_her": list[tuple[str,int]],
      "top_words_me": list[tuple[str,int]],
    }
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT role, content, timestamp FROM chat_history "
            "WHERE contact_name = ? ORDER BY timestamp ASC",
            (contact_name,),
        ).fetchall()
        conn.close()
    except Exception:
        return None

    if not rows:
        return {
            "total_her": 0, "total_me": 0,
            "sufficient": False,
        }

    her_msgs = [r["content"] for r in rows if r["role"] == "her"]
    me_msgs  = [r["content"] for r in rows if r["role"] == "me"]

    total_her = len(her_msgs)
    total_me  = len(me_msgs)

    if total_her + total_me < _MIN_MESSAGES:
        return {
            "total_her": total_her,
            "total_me": total_me,
            "sufficient": False,
        }

    # 过滤噪音后用于特征计算
    her_clean = [t for t in her_msgs if not _is_noise(t)]
    me_clean  = [t for t in me_msgs  if not _is_noise(t)]

    # 平均长度（全量，含噪音，反映真实发送习惯）
    avg_len_her = round(sum(len(t) for t in her_msgs) / total_her, 1) if her_msgs else 0.0
    avg_len_me  = round(sum(len(t) for t in me_msgs)  / total_me,  1) if me_msgs  else 0.0

    # 问句比例（对方，去噪后）
    question_rate_her = (
        round(sum(1 for t in her_clean if "?" in t or "？" in t) / len(her_clean) * 100, 1)
        if her_clean else 0.0
    )

    # 情绪词比例（对方，去噪后）
    positive_rate_her = _rate(her_clean, _POSITIVE_WORDS)
    negative_rate_her = _rate(her_clean, _NEGATIVE_WORDS)

    # 24 小时分布
    hourly_her = [0] * 24
    hourly_me  = [0] * 24
    for r in rows:
        try:
            hour = int(r["timestamp"][11:13])
            if 0 <= hour < 24:
                if r["role"] == "her":
                    hourly_her[hour] += 1
                else:
                    hourly_me[hour] += 1
        except Exception:
            pass

    # 高频词（去噪后）
    top_words_her = _top_words(her_clean, n=10)
    top_words_me  = _top_words(me_clean,  n=10)

    return {
        "total_her": total_her,
        "total_me": total_me,
        "sufficient": True,
        "avg_len_her": avg_len_her,
        "avg_len_me": avg_len_me,
        "question_rate_her": question_rate_her,
        "positive_rate_her": positive_rate_her,
        "negative_rate_her": negative_rate_her,
        "hourly_her": hourly_her,
        "hourly_me": hourly_me,
        "top_words_her": top_words_her,
        "top_words_me": top_words_me,
    }
