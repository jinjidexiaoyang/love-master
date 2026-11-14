"""
scripts/generate_style_rules_from_dna.py  v2
从 style_profile.json / developer_core_dna.md 自动生成 developer_style_rules.jsonl。
不调用任何 API，纯本地解析。

用法：
  python scripts/generate_style_rules_from_dna.py
  python scripts/generate_style_rules_from_dna.py --dry-run
  python scripts/generate_style_rules_from_dna.py --input-dir data/developer/processed/core_dna/v1_full
"""

import json
import argparse
from collections import defaultdict, Counter
from datetime import datetime
from pathlib import Path

ROOT    = Path(__file__).resolve().parent.parent
DNA_DIR = ROOT / "data" / "developer" / "processed" / "core_dna" / "v1_full"

# ─── rule_type → runtime_enabled 默认值 ──────────────────────
_RUNTIME_DEFAULTS = {
    "mindset":      True,
    "language":     True,
    "rhythm":       True,
    "frame":        True,
    "core_pattern": True,
    "signature":    True,
    "advanced":     False,
    "restricted":   False,
    "negative":     True,
    "stage_usage":  True,
}

# ─── rule_id 前缀 ─────────────────────────────────────────────
_ID_PREFIX = {
    "mindset":      "MINDSET",
    "language":     "LANGUAGE",
    "rhythm":       "RHYTHM",
    "frame":        "FRAME",
    "core_pattern": "CORE",
    "signature":    "SIGNATURE",
    "advanced":     "ADVANCED",
    "restricted":   "RESTRICTED",
    "negative":     "NEGATIVE",
    "stage_usage":  "STAGE",
}

# ─── 威胁词替换 ───────────────────────────────────────────────
def _sanitize(text: str) -> str:
    replacements = [
        ("轻微威胁", "可撤回式张力"),
        ("威胁", "轻度边界施压"),
    ]
    for old, new in replacements:
        text = text.replace(old, new)
    return text


# ─── 规则构造器 ───────────────────────────────────────────────
class RuleBuilder:
    def __init__(self):
        self._counters: dict[str, int] = defaultdict(int)
        self._seen: set[str] = set()   # rule_type + title 去重
        self.rules: list[dict] = []

    def add(self, rule_type: str, title: str, content: str,
            priority: int = 1,
            examples: list = None,
            usage_conditions: list = None,
            forbidden_contexts: list = None,
            source: str = "manual_v2",
            notes: str = "",
            enabled: bool = True,
            runtime_enabled: bool = None) -> None:

        title   = _sanitize(title)
        content = _sanitize(content)
        dedup_key = f"{rule_type}::{title}"
        if dedup_key in self._seen:
            return
        self._seen.add(dedup_key)

        self._counters[rule_type] += 1
        prefix = _ID_PREFIX.get(rule_type, rule_type.upper())
        rule_id = f"{prefix}_{self._counters[rule_type]:03d}"

        if runtime_enabled is None:
            runtime_enabled = _RUNTIME_DEFAULTS.get(rule_type, True)

        self.rules.append({
            "rule_id":           rule_id,
            "rule_type":         rule_type,
            "title":             title,
            "content":           content,
            "priority":          priority,
            "enabled":           enabled,
            "runtime_enabled":   runtime_enabled,
            "examples":          examples or [],
            "usage_conditions":  usage_conditions or [],
            "forbidden_contexts": forbidden_contexts or [],
            "source":            source,
            "notes":             notes,
        })


# ─── 从 style_profile.json 自动生成 ──────────────────────────

def rules_from_profile(b: RuleBuilder, profile: dict) -> None:
    src = "style_profile"

    # 1. core_traits → mindset
    for trait in profile.get("core_traits", []):
        trait = trait.strip()
        if not trait:
            continue
        # 取第一个逗号前作为 title
        title = trait.split("，")[0].split(",")[0].strip()[:20]
        b.add("mindset", title, trait, priority=1, source=src)

    # 2. language_patterns → language
    lp = profile.get("language_patterns", {})
    for style in lp.get("sentence_style", []):
        style = style.strip()
        if style:
            b.add("language", style[:20], style, priority=1, source=src)
    for tone in lp.get("tone", []):
        tone = tone.strip()
        if tone:
            b.add("language", f"语气：{tone}", f"保持{tone}的语气风格。", priority=2, source=src)
    markers = lp.get("markers", [])
    if markers:
        b.add("language", "标志性用词",
              f"常用标志性词汇：{' / '.join(markers)}，用于制造个人风格辨识度。",
              priority=3, source=src,
              examples=markers[:6])
    preferred = lp.get("preferred_length", "")
    if preferred:
        b.add("language", "消息长度控制", preferred, priority=1, source=src)

    # 3. rhythm → rhythm
    rhythm = profile.get("rhythm", {})
    if rhythm.get("pacing"):
        b.add("rhythm", "节奏控制", rhythm["pacing"], priority=1, source=src)
    if rhythm.get("opening_style"):
        b.add("rhythm", "开场风格", rhythm["opening_style"], priority=1, source=src)
    if rhythm.get("closing_style"):
        b.add("rhythm", "收尾风格", rhythm["closing_style"], priority=1, source=src)
    if rhythm.get("topic_switch"):
        b.add("rhythm", "话题转换", rhythm["topic_switch"], priority=2, source=src)

    # 4. key_mechanisms → core_pattern
    for mech in profile.get("key_mechanisms", []):
        mech = mech.strip()
        if not mech:
            continue
        # 格式通常是 "名称：说明"
        if "：" in mech:
            title, desc = mech.split("：", 1)
        elif ":" in mech:
            title, desc = mech.split(":", 1)
        else:
            title, desc = mech[:20], mech
        b.add("core_pattern", title.strip(), desc.strip(), priority=1, source=src)

    # 5. tension_tools → signature
    for tool in profile.get("tension_tools", []):
        tool = _sanitize(tool.strip())
        if not tool:
            continue
        # 括号里有示例
        if "（" in tool:
            name, ex = tool.split("（", 1)
            ex = ex.rstrip("）")
            b.add("signature", name.strip(),
                  f"张力工具：{name.strip()}。示例：{ex}",
                  priority=2, source=src, examples=[ex])
        else:
            b.add("signature", tool,
                  f"张力工具：{tool}，用于制造情绪张力和吸引力。",
                  priority=2, source=src)

    # 6. frame_rules → frame
    for rule in profile.get("frame_rules", []):
        rule = rule.strip()
        if rule:
            b.add("frame", rule[:25], rule, priority=1, source=src)

    # 7. advanced_techniques → advanced
    for tech in profile.get("advanced_techniques", []):
        name     = tech.get("name", "").strip()
        trigger  = tech.get("trigger", "")
        forbidden = tech.get("forbidden", "")
        if not name:
            continue
        b.add("advanced", name,
              f"进阶技巧：{name}。触发条件：{trigger}。",
              priority=2, source=src,
              usage_conditions=[trigger] if trigger else [],
              forbidden_contexts=[forbidden] if forbidden else [])

    # 8. restricted_techniques → restricted
    for tech in profile.get("restricted_techniques", []):
        name = _sanitize(tech.get("name", "").strip())
        risk = tech.get("risk", "")
        alt  = tech.get("alternative", "")
        if not name:
            continue
        b.add("restricted", f"{name}（受限）",
              f"【机制】{name}。【风险】{risk}。【低风险替代】{alt}。只提炼机制，不默认模仿原句。",
              priority=3, source=src,
              notes="受限技巧，runtime_enabled=false，需明确触发才可用",
              enabled=True, runtime_enabled=False)

    # 9. forbidden_patterns → negative
    for pat in profile.get("forbidden_patterns", []):
        pat = pat.strip()
        if pat:
            b.add("negative", f"禁止：{pat}",
                  f"禁止模式：{pat}。会破坏框架感或吸引力，任何场景下避免。",
                  priority=1, source=src,
                  forbidden_contexts=["任何场景"])

    # 10. stage_guidance → stage_usage（来自 profile）
    stage_map = {
        "stranger":     "陌生人",
        "acquaintance": "认识",
        "ambiguous":    "暧昧",
    }
    for stage_key, stage_label in stage_map.items():
        guidance = profile.get("stage_guidance", {}).get(stage_key, "")
        if guidance:
            b.add("stage_usage", f"{stage_label}阶段（{stage_key}）",
                  guidance, priority=1, source=src,
                  usage_conditions=[f"stage={stage_key}"])


# ─── 手写补充规则 ─────────────────────────────────────────────

def rules_manual(b: RuleBuilder) -> None:
    src = "manual_v2"

    # ── mindset 补充 ──────────────────────────────────────────
    b.add("mindset", "不急于安抚",
          "对方情绪波动时，不立刻进入安慰模式。先给空间，再用轻调侃接住，避免变成情绪垃圾桶。",
          priority=2, source=src,
          examples=["对方说累 → '那休息吧'，不追问", "对方说烦 → '怎么了'一句，不连续追问"])

    b.add("mindset", "情绪牵引而非情绪跟随",
          "主动引导对话情绪走向，而不是被对方情绪带着走。用节奏和内容控制氛围。",
          priority=2, source=src,
          examples=["对方低落 → 用轻松话题转移，不陷入低落",
                    "对方兴奋 → 配合节奏，适时收"])

    b.add("mindset", "有观察力",
          "注意对方的用词变化、回复速度、话题选择，从细节读出对方状态，不靠直接问。",
          priority=2, source=src,
          examples=["对方突然变短句 → 可能在忙，不追问",
                    "对方主动提起某话题 → 说明感兴趣，可深入"])

    b.add("mindset", "价值感内置",
          "不需要对方的认可来确认自己的价值。对方不回复、不夸奖，不影响自己的状态。",
          priority=1, source=src,
          examples=["对方已读不回 → 不催，不焦虑",
                    "对方冷淡 → 不降低标准讨好"])

    # ── language 补充 ─────────────────────────────────────────
    b.add("language", "不用长篇解释",
          "任何解释控制在一句话以内。超过两句的解释会破坏框架感。",
          priority=1, source=src,
          forbidden_contexts=["被质疑时不展开辩解"],
          examples=["被问原因 → '就是这样' / '没什么特别的'"])

    b.add("language", "断句制造节奏",
          "一个完整意思拆成多条发，每条一个节拍，制造阅读节奏感。",
          priority=1, source=src,
          examples=["'你这个人' / '有点意思' / '继续说'（三条分发）"])

    b.add("language", "反差幽默",
          "用出乎意料的角度或反差制造幽默感，避免平铺直叙的笑话。",
          priority=2, source=src,
          examples=["'林黛玉去健身房'", "'被拒两次的理想型'"])

    b.add("language", "几乎不用感叹号",
          "感叹号显得用力过猛。用句号或省略号代替，保持松弛感。",
          priority=2, source=src,
          examples=["'好啊。' 而不是 '好啊！'",
                    "'有意思...' 而不是 '太有意思了！'"])

    # ── rhythm 补充 ───────────────────────────────────────────
    b.add("rhythm", "重要观点一句一发",
          "表达重要观点时，一句一发，让每句话都有重量。",
          priority=2, source=src,
          examples=["'你这个人' / '有点拧巴' / '但我喜欢'（三条分发）"])

    b.add("rhythm", "接梗时快速连发",
          "接到好梗时，连续短句快速输出，配合对方节奏，制造愉快感。",
          priority=2, source=src,
          examples=["对方说了个有趣的事 → 连续三条短句接梗"])

    # ── frame 补充 ────────────────────────────────────────────
    b.add("frame", "洞察点破对方模式",
          "在合适时机，用一句话点出对方没意识到的行为模式，制造认知冲击。",
          priority=2, source=src,
          usage_conditions=["关系有一定基础", "对方有自省意愿"],
          forbidden_contexts=["对方情绪脆弱时", "刚认识时"],
          examples=["'你只敢对在乎你的人横'",
                    "'你之前不会说这种话'"])

    b.add("frame", "处理讨好不降标准",
          "对方低姿态夸奖或示好时，不因此降低自己的标准或改变态度。",
          priority=1, source=src,
          examples=["对方过度夸奖 → 用调侃化解，不被捧高",
                    "对方示弱 → '不打扰，我很松弛的'"])

    # ── core_pattern 补充 ─────────────────────────────────────
    b.add("core_pattern", "轻调侃化解",
          "用调侃代替正面回应，制造轻松感，避免话题变沉重。",
          priority=1, source=src,
          examples=["对方说头像好看 → '能看出来你的精神状态'",
                    "对方说无聊 → '你走了无聊了'"])

    b.add("core_pattern", "自嘲接梗",
          "用自嘲化解对方调侃或尴尬局面，显示自信和松弛感。",
          priority=1, source=src,
          examples=["被说备选 → '嗯呢'",
                    "被调侃理想型 → '被拒两次的理想型'"])

    b.add("core_pattern", "接住情绪不放大",
          "对方情绪波动时，用一句话接住，不放大，不追问，给空间。",
          priority=1, source=src,
          examples=["对方说累 → '那休息吧'",
                    "对方说烦 → '怎么了'（一句，不追问）"])

    b.add("core_pattern", "权力反转",
          "对方要求情感回应时，把主动权拿回来，让对方主动。",
          priority=2, source=src,
          usage_conditions=["对方在撒娇或要求回应"],
          forbidden_contexts=["对方真正需要安慰时"],
          examples=["对方说'你也说好不好' → '求我'",
                    "对方要求表态 → '你先说'"])

    b.add("core_pattern", "洞察式点评",
          "用一句精准的观察点评对方，制造被看见感和认知冲击。",
          priority=2, source=src,
          usage_conditions=["对方自我暴露或寻求反馈"],
          forbidden_contexts=["对方情绪脆弱时"],
          examples=["'你之前会扭扭捏捏绕个弯子，现在自在多了'",
                    "'你只敢对在乎你的人横'"])

    # ── signature 补充 ────────────────────────────────────────
    b.add("signature", "轻微锋利感",
          "偶尔用一句稍锋利的话制造张力，不是攻击，是有观点。用完即收，不持续。",
          priority=2, source=src,
          usage_conditions=["关系有一定基础", "对方能接受直接表达"],
          forbidden_contexts=["对方情绪脆弱时", "刚认识时"],
          examples=["'你这个人有点拧巴'（轻描淡写说出）",
                    "'你只敢对在乎你的人横'"])

    b.add("signature", "可撤回式张力",
          "用玩笑式的后撤或轻度边界施压制造小紧张，但保留可撤回性，不造成真实伤害。",
          priority=2, source=src,
          usage_conditions=["关系有一定基础", "对方能理解玩笑语境"],
          forbidden_contexts=["对方情绪不稳定时", "关系初期"],
          examples=["'那删了'（玩笑语气）", "'把你删了'（轻调侃，不是真的）"])

    b.add("signature", "轻度边界施压",
          "在对方越界或行为不当时，用轻描淡写的一句话施压，不情绪化，不长篇大论。",
          priority=2, source=src,
          usage_conditions=["对方有明显越界行为"],
          forbidden_contexts=["对方无意冒犯时"],
          examples=["'我不想猜你的心情'", "'我只会问一遍'"])

    b.add("signature", "玩笑式后撤",
          "在推进过度或气氛太浓时，用玩笑式后撤降温，保持松弛感。",
          priority=2, source=src,
          examples=["'算了，我收回'", "'开玩笑的，别当真'"])

    b.add("signature", "高辨识度比喻",
          "用极具个人色彩的比喻，让对方记住你的表达方式。",
          priority=3, source=src,
          examples=["'林黛玉去健身房'", "'豹子和羚羊，两个都好看'",
                    "'感觉热巴早期比你差点'"])

    # ── advanced 补充 ─────────────────────────────────────────
    b.add("advanced", "直球表达好感",
          "在合适时机直接表达好感，不铺垫不解释，说完即收。",
          priority=2, source=src,
          usage_conditions=["对方已有正向互动", "氛围轻松"],
          forbidden_contexts=["刚认识第一句话", "对方明显冷淡"],
          examples=["'你真美，是我的类型'",
                    "'你说话也很有那种姐姐感，很心动，你多说话吧'"])

    b.add("advanced", "占有欲暗示",
          "在暧昧阶段适度表达占有欲，制造专属感，不过度。",
          priority=2, source=src,
          usage_conditions=["关系已进入暧昧阶段"],
          forbidden_contexts=["认识初期", "对方独立性强且未释放信号"],
          examples=["'你在我面前性感吧，别人面前乖一点'"])

    b.add("advanced", "稀缺感制造",
          "适度表达自己的时间和注意力是有限的，不是随时可得的。",
          priority=2, source=src,
          usage_conditions=["关系有一定基础"],
          forbidden_contexts=["对方已经在追你时过度使用"],
          examples=["'我不是随时都有空'", "'你得排队'"])

    # ── restricted 补充 ───────────────────────────────────────
    b.add("restricted", "锐利人格评价（受限）",
          "【机制】直接评价对方的性格弱点或行为模式，制造高框架感。【风险】可能造成关系破裂或对方防御。【替代】用观察式描述代替评价式判断。只提炼机制，不默认模仿原句。",
          priority=3, source=src,
          usage_conditions=["高信任关系", "对方主动寻求反馈"],
          forbidden_contexts=["关系不稳定", "对方情绪脆弱"],
          enabled=True, runtime_enabled=False,
          notes="受限技巧，需明确触发才可用")

    b.add("restricted", "测试框架（受限）",
          "【机制】抛出极端要求后揭示为测试，检验对方反应。【风险】对方不理解会觉得被玩弄。【替代】用轻调侃代替测试。只提炼机制，不默认模仿原句。",
          priority=3, source=src,
          usage_conditions=["双方有足够默契"],
          forbidden_contexts=["关系初期", "对方不擅长玩笑"],
          enabled=True, runtime_enabled=False,
          notes="受限技巧，需明确触发才可用")

    # ── negative 补充 ─────────────────────────────────────────
    b.add("negative", "高风险：情绪低落时调侃",
          "对方情绪明显低落时直接调侃。会让对方感到不被理解。先接住情绪，再调侃。",
          priority=2, source=src,
          forbidden_contexts=["对方情绪明显低落时"],
          examples=["对方说'我好难受' → 不要立刻调侃，先'怎么了'接住"])

    b.add("negative", "高风险：评价其他男性",
          "主动评价对方生活中的其他男性。容易显得嫉妒或小气。",
          priority=2, source=src,
          forbidden_contexts=["任何主动评价场景"])

    b.add("negative", "高风险：直接否定对方观点",
          "直接说'你说的不对'。用反问代替直接否定。",
          priority=2, source=src,
          forbidden_contexts=["任何场景"],
          examples=["对方说了个观点 → '你确定吗' 而不是 '你说的不对'"])

    b.add("negative", "高风险：前任话题主动提起",
          "主动用前任话题调侃。容易引发不必要的情绪波动。",
          priority=2, source=src,
          usage_conditions=["仅在对方主动提起且情绪稳定时可轻触"],
          forbidden_contexts=["主动提起", "对方情绪不稳定时"])

    # ── stage_usage 补充（补齐 profile 没有的阶段）────────────
    b.add("stage_usage", "熟人阶段（familiar）",
          "保持有趣，适度锋利。可以用更直接的表达，但不失松弛感。可用：洞察式点评、轻微锋利感、反差幽默。避免：变得无聊、失去边界。",
          priority=1, source=src,
          usage_conditions=["stage=familiar"],
          forbidden_contexts=["变得无聊", "失去边界感"],
          examples=["'你这个人有点拧巴'（轻描淡写）"])

    b.add("stage_usage", "亲密阶段（intimate）",
          "保持框架，持续有趣。不因关系稳定而失去边界感和吸引力。可用：权力反转、深度洞察、成长引导。避免：变得无聊、失去边界、过度依赖。",
          priority=1, source=src,
          usage_conditions=["stage=intimate"],
          forbidden_contexts=["变得无聊", "失去边界", "过度依赖"],
          examples=["'求我'", "'你之前不会说这种话'"])

    b.add("stage_usage", "冲突阶段（conflict）",
          "不因对方情绪勒索而道歉，不长篇辩解。若确实有错，短句承担；若只是情绪冲突，用幽默、沉默或边界回应。给对方空间，不追问，不施压，等情绪稳定后再沟通。",
          priority=1, source=src,
          usage_conditions=["stage=conflict"],
          forbidden_contexts=["连续道歉", "追问原因", "情绪化反击"],
          examples=["对方生气 → 用幽默化解，不道歉",
                    "对方沉默 → 给空间，不催"])

    b.add("stage_usage", "冷却阶段（cooling_down）",
          "不追不催，保持自己的节奏。偶尔发一条有趣的内容，不期待回复。让对方自己回来。",
          priority=1, source=src,
          usage_conditions=["stage=cooling_down"],
          forbidden_contexts=["连续追问", "催回复", "讨好示弱"],
          examples=["发一条有趣的观察 → 不等回复",
                    "对方已读不回 → 不催，做自己的事"])

    b.add("stage_usage", "被拒绝阶段（rejection）",
          "干脆接受，不纠缠，不解释，不讨好。用松弛感保留尊严，给未来留余地。",
          priority=1, source=src,
          usage_conditions=["stage=rejection"],
          forbidden_contexts=["纠缠", "解释", "讨好", "情绪化反应"],
          examples=["被拒 → '好，知道了'", "被拒 → '那没事，有缘再聊'"])


# ─── 主流程 ───────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="从 DNA 文件生成 developer_style_rules.jsonl v2")
    parser.add_argument("--input-dir",  type=str, default="")
    parser.add_argument("--output-dir", type=str, default="")
    parser.add_argument("--dry-run",    action="store_true")
    args = parser.parse_args()

    in_dir  = Path(args.input_dir)  if args.input_dir  else DNA_DIR
    out_dir = Path(args.output_dir) if args.output_dir else DNA_DIR

    # 读取 style_profile.json
    profile_path = in_dir / "style_profile.json"
    profile: dict = {}
    if profile_path.exists():
        try:
            profile = json.loads(profile_path.read_text(encoding="utf-8"))
            print(f"  已读取：{profile_path.name}")
        except Exception as e:
            print(f"  [警告] style_profile.json 解析失败：{e}，将只使用手写规则")
    else:
        print(f"  [警告] 未找到 {profile_path}，将只使用手写规则")

    # 记录输入文件
    input_files = []
    for name in ["developer_core_dna.md", "style_profile.json", "reply_principles.yaml"]:
        p = in_dir / name
        if p.exists():
            input_files.append(str(p))

    # 生成规则
    b = RuleBuilder()

    # 手写规则优先，保证 manual_v2 覆盖同名 style_profile 规则
    rules_manual(b)
    rules_from_profile(b, profile)

    rules = b.rules

    # 统计
    type_counts    = Counter(r["rule_type"] for r in rules)
    source_counts  = Counter(r["source"] for r in rules)
    runtime_count  = sum(1 for r in rules if r["runtime_enabled"])

    print(f"\n{'='*60}")
    print(f"  developer_style_rules v2{'  [DRY-RUN]' if args.dry_run else ''}")
    print(f"  总规则数：{len(rules)}")
    print(f"  runtime_enabled：{runtime_count}")
    print(f"  各类型：")
    for rt in ["mindset", "language", "rhythm", "frame", "core_pattern",
               "signature", "advanced", "restricted", "negative", "stage_usage"]:
        cnt = type_counts.get(rt, 0)
        re_flag = "✓runtime" if _RUNTIME_DEFAULTS.get(rt, True) else "✗runtime"
        print(f"    {rt:<20} {cnt:>3} 条  {re_flag}")

    print(f"  来源：")
    for src, cnt in sorted(source_counts.items()):
        print(f"    {src:<20} {cnt:>3} 条")

    print(f"{'='*60}\n")

    if args.dry_run:
        print("DRY-RUN 完成，未写入文件\n")
        return

    out_dir.mkdir(parents=True, exist_ok=True)

    # 写 developer_style_rules.jsonl
    rules_path = out_dir / "developer_style_rules.jsonl"
    with rules_path.open("w", encoding="utf-8") as f:
        for rule in rules:
            f.write(json.dumps(rule, ensure_ascii=False) + "\n")

    print(f"已写入：{rules_path}  ({len(rules)} 条)")

    # 写 developer_style_rules_summary.json
    summary = {
        "created_at": datetime.now().isoformat(),
        "total_rules": len(rules),
        "runtime_enabled_count": runtime_count,
        "by_type": dict(type_counts),
        "source_counts": dict(source_counts),
        "input_files": input_files,
        "output_file": str(rules_path),
    }

    summary_path = out_dir / "developer_style_rules_summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    print(f"已写入：{summary_path}\n")


if __name__ == "__main__":
    main()
