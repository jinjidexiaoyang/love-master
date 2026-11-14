"""
scripts/select_developer_dialogue_samples.py
开发者聊天素材高质量片段筛选脚本 v2

分类体系：
  core_style    → 低风险可泛化，可进核心 DNA
  advanced_style → 中等风险，熟人/暧昧阶段参考
  restricted    → 高风险但有风格研究价值，不进核心 DNA
  negative      → 明确反例，不学习

输出目录：
  data/developer/processed/selected_dialogues/runs/<run_id>/
  data/developer/processed/selected_dialogues/latest/

用法：
  python scripts/select_developer_dialogue_samples.py --dry-run
  python scripts/select_developer_dialogue_samples.py --limit-files 2
  python scripts/select_developer_dialogue_samples.py
"""

import sys
import os
import re
import json
import shutil
import argparse
import hashlib
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from config import get_deepseek_config as _get_ds_cfg
_ds_cfg = _get_ds_cfg()

# ─── DeepSeek 配置（仅本脚本，不影响主业务链路）─────────────
DEEPSEEK_API_KEY  = _ds_cfg.api_key
DEEPSEEK_MODEL    = _ds_cfg.model
DEEPSEEK_BASE_URL = _ds_cfg.base_url

# ─── 默认目录 ─────────────────────────────────────────────────
DEFAULT_RAW_DIR = ROOT / "data" / "developer" / "raw_chats"
DEFAULT_OUT_DIR = ROOT / "data" / "developer" / "processed" / "selected_dialogues"

# ─── 隐私脱敏正则（精准版，避免误伤普通中文）────────────────
_PRIVACY_PATTERNS = [
    # 经纬度：数字.数字, 数字.数字 格式
    (re.compile(r'\(?\d{2,3}\.\d{4,8}\s*,\s*\d{2,3}\.\d{4,8}\)?'), '[位置]'),
    # 手机号：1开头11位
    (re.compile(r'(?<!\d)1[3-9]\d{9}(?!\d)'), '[联系方式]'),
    # 身份证：17位数字+校验位
    (re.compile(r'(?<!\d)\d{17}[\dXx](?!\d)'), '[隐私信息]'),
    # 酒店品牌名（只替换品牌名本身，不做宽泛后缀匹配）
    (re.compile(r'(汉庭酒店|如家酒店|7天酒店|速8酒店|全季酒店|亚朵酒店|希尔顿酒店|万豪酒店|'
                r'喜来登酒店|维也纳酒店|格林豪泰|锦江酒店|华住酒店|雅高酒店|'
                r'汉庭|如家|速8|全季|亚朵|希尔顿|万豪|喜来登|格林豪泰|锦江之星)'
                r'[^\n，。！？]{0,30}'), '[位置]'),
    # 详细门牌地址：必须同时含数字+路/街/道/号，避免误伤纯文字句子
    # 格式：X路X号 / X街X号 / X号楼X室 等，要求含数字
    (re.compile(r'[^\s，。！？]{1,8}(路|街|道|巷)\d+号'), '[地址]'),
    (re.compile(r'\d+(号楼|栋|单元|室|层|楼)\d*'), '[地址]'),
    # 小区名：XX小区/XX花园/XX苑 + 数字楼栋
    (re.compile(r'[^\s，。！？]{2,8}(小区|花园|苑|公寓|大厦)\d*(号楼|栋|单元)?'), '[地址]'),
]

_TS_HEADER    = re.compile(r"^(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})\s+'(.+?)'$")
_VOICE_PREFIX = re.compile(r"^\[语音转文字\]\s*")


def desensitize(text: str) -> str:
    for pattern, replacement in _PRIVACY_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def extract_name_from_filename(filename: str) -> str:
    stem = Path(filename).stem
    for prefix in ("私聊_", "chat_", "群聊_"):
        if stem.startswith(prefix):
            stem = stem[len(prefix):]
            break
    if " " in stem:
        return stem.split(" ")[0].strip()
    if "_" in stem:
        parts = stem.split("_")
        first = parts[0].strip()
        if first and not first.isdigit():
            return first
    return stem.strip()


def parse_messages(text: str) -> list:
    messages = []
    blocks = re.split(r'\n\s*\n', text.strip())
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        lines = block.splitlines()
        m = _TS_HEADER.match(lines[0].strip())
        if not m:
            continue
        ts      = m.group(1)
        speaker = m.group(2).strip()
        content_lines = [l.strip() for l in lines[1:] if l.strip()]
        content = " ".join(content_lines)
        content = _VOICE_PREFIX.sub("", content).strip()
        if not content:
            continue
        role = "user" if speaker == "我" else "other"
        messages.append({"role": role, "speaker": speaker,
                         "content": content, "timestamp": ts})
    return messages


def make_windows(messages: list, window_size: int, overlap: int) -> list:
    windows = []
    step = max(1, window_size - overlap)
    i = 0
    while i < len(messages):
        chunk = messages[i: i + window_size]
        if len(chunk) >= 5:
            windows.append((i, chunk))
        i += step
    return windows


def window_fingerprint(msgs: list) -> str:
    raw = "".join(m["content"] for m in msgs)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def format_window_for_prompt(msgs: list, contact_name: str) -> str:
    lines = []
    for m in msgs:
        label   = "我" if m["role"] == "user" else contact_name
        content = desensitize(m["content"])
        lines.append(f"{m['timestamp']} [{label}]: {content}")
    return "\n".join(lines)


# ─── 后处理降级规则（关键词兜底，不依赖 DeepSeek 判断准确）──
# 降级到 restricted 的关键词
_RESTRICTED_KEYWORDS = [
    "睡", "摸", "宝宝", "酒店", "喝酒", "醉", "诱惑", "坏学生", "流氓",
    "亲", "抱", "性", "前任", "吊着", "多线", "拧巴", "端着",
    "放不下", "握不住", "吸渣", "耗光", "没珍惜",
]

# 降级到 advanced 的关键词
_ADVANCED_KEYWORDS = [
    "喜欢", "心动", "攻略", "姐姐", "破例", "嫁你", "娶我", "入赘",
    "腰", "身材", "细细", "理想型", "想你", "忙完找我", "有空来找我",
    "很美式",
]


def _dialogue_text(item: dict) -> str:
    """提取 dialogue 所有内容合并成一个字符串，用于关键词匹配"""
    return " ".join(msg.get("content", "") for msg in item.get("dialogue", []))


def _downgrade_core(item: dict, current_type: str) -> str:
    """
    对 DeepSeek 返回的 core_style 片段做关键词兜底检查。
    返回最终应该归入的 case_type。
    """
    if current_type != "core_style":
        return current_type

    text = _dialogue_text(item)

    # 优先检查 restricted 关键词
    for kw in _RESTRICTED_KEYWORDS:
        if kw in text:
            return "restricted"

    # 再检查 advanced 关键词
    for kw in _ADVANCED_KEYWORDS:
        if kw in text:
            return "advanced_style"

    # quality_score < 9 且 risk_level = medium 也降级到 advanced
    score = item.get("quality_score", 0)
    risk  = item.get("risk_level", "low")
    if score < 9 and risk == "medium":
        return "advanced_style"

    return "core_style"


def _postprocess_item(item: dict) -> dict:
    """
    v4 后处理：在写入任何文件前统一修正字段一致性。
    不改变分类逻辑，只修正字段值。
    """
    case_type = item.get("case_type", "")

    # Fix 2: restricted 必须是 risk_level=high
    if case_type == "restricted":
        item["risk_level"] = "high"
        if not item.get("risk", "").strip():
            item["risk"] = "该样本被归入 restricted，存在高场景依赖或高风险，不应进入核心 DNA。"

    # Fix 3: distill_eligible 由本地逻辑决定，不信任 DeepSeek 返回值
    item["distill_eligible"] = (case_type == "core_style")

    # Fix 4: core_style 的 style_mechanism 为空时自动填充
    if case_type == "core_style" and not item.get("style_mechanism", "").strip():
        tags = item.get("tags", [])
        scene = item.get("scene", "")
        tags_str = "、".join(tags[:3]) if tags else ""
        if tags_str:
            item["style_mechanism"] = f"通过{scene or '简短回应'}保持松弛感和不低姿态，体现{tags_str}。"
        else:
            item["style_mechanism"] = "通过简短自然的回应保持轻松节奏，不解释、不讨好。"

    return item


# ─── DeepSeek Prompt ──────────────────────────────────────────
SYSTEM_PROMPT = """你是一个聊天风格分析专家，专门帮助开发者从自己的历史聊天记录中提炼风格素材。

## 核心原则

**有风格 ≠ 可进入核心 DNA。**
**质量分高 ≠ 可进入核心 DNA。**
**风险优先级高于质量分。**

你的任务是把片段分成四类：

---

### core_style（核心风格样本）

唯一允许进入默认核心 DNA 的类型。门槛极高，必须同时满足全部条件：

✅ 必须满足：
- quality_score >= 9（8分片段只有在极度低风险且高度泛化时才可例外）
- risk_level = low 或 medium
- 陌生人/认识阶段即可使用，不依赖强关系基础
- 短句、轻调侃、接梗、松弛自然、不低姿态、不过度解释、有边界

❌ 以下任意一条出现，立即排除出 core，降级到 advanced 或 restricted：
- 含"喜欢""心动""破例""为你""想你""理想型"等直球表白词
- 含"攻略""姐姐感""嫁你""娶我""入赘"等亲密关系调侃
- 含"腰""身材""细细""很美式"等身体评价
- 含"拧巴""端着""放不下前任""握不住""吸渣""耗光""没珍惜"等对对方人格/行为的锐利评价
- 含"自己握不住就别说没有"等高框架反击
- 含"忙完找我""有空来找我"等带推进意味的邀约
- 含任何性暗示、身体接触、酒店、深夜见面
- 含"宝宝""亲""抱""摸"等亲密称呼或动作
- 含"吊着""前任""多线"等关系博弈词
- 需要对方先有明显正向反馈才能使用

---

### advanced_style（进阶风格案例）

中等风险，有风格价值，适合熟人/暧昧阶段参考。默认不进入核心 DNA。

包含：
- 直球喜欢（"喜欢""心动""破例""为你"）
- 亲密关系调侃（"嫁你""娶我""入赘""攻略姐姐"）
- 身体评价（"腰""身材""细细"）
- 轻度推进（"忙完找我""有空来找我"）
- 有框架感的反击（但不含人格攻击）
- 需要对方有正向反馈才能使用的表达

---

### restricted（受限案例）

高风险，有风格研究价值，绝对不进入核心 DNA。

包含：
- 强性张力、强直球、性暗示
- 酒店/深夜见面邀约
- 对对方人格/行为的锐利评价（"拧巴""端着""放不下前任""握不住"）
- 高框架反击（"自己握不住就别说没有"）
- 冷回应、锐利评价
- 多线关系相关
- 高度私密关系中的表达
- 宝宝/亲/抱/摸等亲密称呼或动作
- 可能冒犯普通用户的内容

这类片段保留是为了研究"风格机制"，不是为了直接学习原句。
每条 restricted 必须填写 style_mechanism（可抽象出的风格机制）。

---

### negative（负面样本）

明确不应该学习的反例：
- 低姿态讨好、连环追问、催回复
- 情绪化纠缠、过度解释、道歉连篇
- 无视拒绝、明显施压
- 对方已经冷淡还继续强推
- 自我价值感崩塌、过度暴露需求感

---

## 铁律

1. 绝对不得编造任何内容
2. 绝对不得改写原话，dialogue 字段必须是原话
3. 绝对不得美化或润色原话
4. 必须脱敏：酒店名、地址、经纬度、电话、身份信息 → 统一替换为 [位置][地址][联系方式][隐私信息]
5. 如果没有高价值片段，返回空数组 []
6. 只输出 JSON，不要任何额外文字
7. 如果一个片段既有风格价值又高风险，归入 restricted，不要归入 core_style
8. 宁可少选 core，也不要把中高风险片段放进 core

## risk_level 定义

- low：任何关系阶段都可以用，不会引起误解或不适
- medium：需要一定关系基础，可能在陌生人阶段显得唐突
- high：强场景依赖，错误使用会造成明显负面效果"""


def build_user_prompt(window_text: str, contact_name: str, min_quality: int) -> str:
    return f"""请分析以下聊天片段（联系人：{contact_name}），识别其中有价值的对话片段。

只返回 quality_score >= {min_quality} 的片段。
如果没有符合条件的片段，返回 []。

【聊天片段】
{window_text}

输出 JSON 数组，每个元素格式：
{{
  "case_type": "core_style | advanced_style | restricted | negative",
  "distill_eligible": true或false,
  "risk_level": "low | medium | high",
  "quality_score": 1到10的整数,
  "scene": "场景描述，10字以内",
  "stage": "陌生人/认识/熟人/暧昧/上头/痴迷",
  "dialogue": [
    {{"role": "user", "content": "原话，不得改写，必须脱敏"}},
    {{"role": "other", "content": "原话，不得改写，必须脱敏"}}
  ],
  "why_good": "为什么有价值（core/advanced/restricted）或为什么是反例（negative），50字以内",
  "style_mechanism": "可抽象出的风格机制，例如：不低姿态接梗、轻调侃制造张力（restricted类必填）",
  "usage_conditions": ["适用条件1", "适用条件2"],
  "forbidden_contexts": ["禁用场景1", "禁用场景2"],
  "tags": ["标签1", "标签2"],
  "risk": "使用风险，没有则填空字符串"
}}

分类规则：
- core_style → distill_eligible=true，risk_level必须是low或medium
- advanced_style → distill_eligible=false，risk_level=medium
- restricted → distill_eligible=false，risk_level=high
- negative → distill_eligible=false

注意：dialogue 只包含该片段的核心消息，不要把整个窗口都放进去。"""


# ─── DeepSeek API 调用 ────────────────────────────────────────
def call_deepseek(window_text: str, contact_name: str, min_quality: int) -> list | None:
    try:
        from openai import OpenAI
        client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)
        resp = client.chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": build_user_prompt(window_text, contact_name, min_quality)},
            ],
            temperature=0.1,
            max_tokens=3000,
        )
        raw = resp.choices[0].message.content.strip()
        if "```json" in raw:
            raw = raw.split("```json")[1].split("```")[0].strip()
        elif "```" in raw:
            raw = raw.split("```")[1].split("```")[0].strip()
        result = json.loads(raw)
        return result if isinstance(result, list) else []
    except json.JSONDecodeError as e:
        print(f"    [JSON解析失败] {e}")
        return None
    except Exception as e:
        print(f"    [DeepSeek错误] {e}")
        return None


# ─── 输出写入 ─────────────────────────────────────────────────
def _dialogue_lines(item: dict, contact_name: str) -> str:
    lines = []
    for msg in item.get("dialogue", []):
        label   = "我" if msg["role"] == "user" else contact_name
        content = desensitize(msg.get("content", ""))
        lines.append(f"{label}：{content}")
    return "\n".join(lines)


def write_core_sample(f, item: dict, contact_name: str, sample_id: str):
    tags_str = "\n".join(f"- {t}" for t in item.get("tags", [])) or "- 无"
    f.write(f"""【样本ID】{sample_id}
【联系人】{contact_name}
【场景】{item.get('scene', '')}
【关系阶段】{item.get('stage', '')}
【质量分】{item.get('quality_score', 0)}/10  【风险等级】{item.get('risk_level', '')}

【原始对话】
{_dialogue_lines(item, contact_name)}

【为什么值得学习】
{item.get('why_good', '')}

【风格机制】
{item.get('style_mechanism', '')}

【可学习点】
{tags_str}

【风险】
{item.get('risk') or '无'}

{'─' * 60}

""")


def write_negative_sample(f, item: dict, contact_name: str, sample_id: str):
    f.write(f"""【负面样本ID】{sample_id}
【联系人】{contact_name}
【场景】{item.get('scene', '')}

【原始对话】
{_dialogue_lines(item, contact_name)}

【为什么是负面样本】
{item.get('why_good', '')}

【标签】
{chr(10).join('- ' + t for t in item.get('tags', [])) or '- 无'}

{'─' * 60}

""")


def make_case_obj(item: dict, sample_id: str, contact_name: str, source_file: str) -> dict:
    # 对 dialogue 内容脱敏
    dialogue = []
    for msg in item.get("dialogue", []):
        dialogue.append({
            "role":    msg.get("role", ""),
            "content": desensitize(msg.get("content", "")),
        })
    case_type = item.get("case_type", "core_style")
    distill   = item.get("distill_eligible", case_type == "core_style")
    return {
        "case_id":          sample_id,
        "contact_name":     contact_name,
        "source_file":      source_file,
        "case_type":        case_type,
        "distill_eligible": distill,
        "risk_level":       item.get("risk_level", "low"),
        "scene":            item.get("scene", ""),
        "stage":            item.get("stage", ""),
        "dialogue":         dialogue,
        "why_good":         item.get("why_good", ""),
        "style_mechanism":  item.get("style_mechanism", ""),
        "usage_conditions": item.get("usage_conditions", []),
        "forbidden_contexts": item.get("forbidden_contexts", []),
        "tags":             item.get("tags", []),
        "risk":             item.get("risk", ""),
        "quality_score":    item.get("quality_score", 0),
    }


# ─── 主流程 ───────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="开发者聊天素材高质量片段筛选 v2")
    parser.add_argument("--dry-run",        action="store_true", help="只解析切片，不调用DeepSeek，不写文件")
    parser.add_argument("--limit-files",    type=int, default=0, help="只处理前N个文件（0=全部）")
    parser.add_argument("--start-window",   type=int, default=1, help="从第N个窗口开始处理（1=从头，101=跳过前100）")
    parser.add_argument("--limit-windows",  type=int, default=0, help="本次最多处理N个窗口（0=全部）")
    parser.add_argument("--window-size",    type=int, default=30, help="窗口消息条数（默认30）")
    parser.add_argument("--overlap",       type=int, default=5,  help="窗口重叠条数（默认5）")
    parser.add_argument("--min-quality",   type=int, default=8,  help="最低质量分（默认8）")
    parser.add_argument("--run-id",        type=str, default="", help="自定义run_id（默认时间戳）")
    parser.add_argument("--input-dir",     type=str, default="", help="自定义输入目录")
    parser.add_argument("--output-dir",    type=str, default="", help="自定义输出基础目录")
    args = parser.parse_args()

    raw_dir = Path(args.input_dir)  if args.input_dir  else DEFAULT_RAW_DIR
    out_base = Path(args.output_dir) if args.output_dir else DEFAULT_OUT_DIR
    raw_dir.mkdir(parents=True, exist_ok=True)

    if not args.dry_run and not DEEPSEEK_API_KEY:
        print("\n[错误] 未找到 DEEPSEEK_API_KEY，请在 .env 中配置")
        print("或使用 --dry-run 模式预览\n")
        sys.exit(1)

    txt_files = sorted(raw_dir.glob("*.txt"))
    if not txt_files:
        print(f"\n[提示] {raw_dir} 下没有 .txt 文件\n")
        sys.exit(0)

    if args.limit_files > 0:
        txt_files = txt_files[: args.limit_files]

    # 确定输出目录
    run_id  = args.run_id or datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = out_base / "runs" / run_id
    lat_dir = out_base / "latest"

    mode = "[DRY-RUN 预览]" if args.dry_run else f"[正式筛选 · run={run_id}]"
    lw_hint = ""
    if args.start_window > 1 or args.limit_windows > 0:
        sw = args.start_window
        lw = args.limit_windows
        lw_hint = f"  窗口范围：第{sw}个起" + (f"，最多{lw}个" if lw > 0 else "，全部") + "\n"
    print(f"\n{'='*64}")
    print(f"  开发者素材筛选 v2  {mode}")
    print(f"  文件：{len(txt_files)} 个  窗口：{args.window_size}条  重叠：{args.overlap}条  最低分：{args.min_quality}")
    if lw_hint:
        print(lw_hint, end="")
    print(f"  输入：{raw_dir}")
    if not args.dry_run:
        print(f"  输出：{run_dir}")
    print(f"{'='*64}\n")

    log = {
        "run_id":   run_id,
        "run_time": datetime.now().isoformat(),
        "mode":     "dry_run" if args.dry_run else "full",
        "params":   {"window_size": args.window_size, "overlap": args.overlap,
                     "min_quality": args.min_quality,
                     "start_window": args.start_window,
                     "limit_windows": args.limit_windows},
        "files":    [],
        "summary":  {},
    }

    seen_fps: set = set()
    cnt = {"core": 0, "advanced": 0, "restricted": 0, "negative": 0, "fail": 0}

    # 正式模式才创建目录和文件
    if not args.dry_run:
        run_dir.mkdir(parents=True, exist_ok=True)
        core_f     = open(run_dir / "core_style_samples.txt",      "w", encoding="utf-8")
        compat_f   = open(run_dir / "style_samples_high_quality.txt", "w", encoding="utf-8")
        adv_f      = open(run_dir / "advanced_style_cases.jsonl",   "w", encoding="utf-8")
        rest_f     = open(run_dir / "restricted_cases.jsonl",       "w", encoding="utf-8")
        neg_f      = open(run_dir / "negative_samples.txt",         "w", encoding="utf-8")
        case_f     = open(run_dir / "case_cards.jsonl",             "w", encoding="utf-8")
    else:
        core_f = compat_f = adv_f = rest_f = neg_f = case_f = None

    try:
        for fp in txt_files:
            contact_name = extract_name_from_filename(fp.name)
            print(f"[{fp.name}]  联系人：{contact_name}")

            try:
                text = fp.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                text = fp.read_text(encoding="gbk")

            messages = parse_messages(text)
            windows  = make_windows(messages, args.window_size, args.overlap)
            total_windows  = len(windows)
            start_idx      = max(0, args.start_window - 1)          # 转 0-based
            windows_after  = windows[start_idx:]                    # 跳过前 start_window-1 个
            limit_windows  = args.limit_windows if args.limit_windows > 0 else len(windows_after)
            windows_to_run = windows_after[:limit_windows]

            file_log = {
                "file": fp.name, "contact": contact_name,
                "total_messages":    len(messages),
                "total_windows":     total_windows,
                "start_window":      args.start_window,
                "limit_windows":     args.limit_windows,
                "processed_windows": len(windows_to_run),
                "deepseek_calls": 0,
                "core_style": 0, "advanced_style": 0,
                "restricted": 0, "negative": 0, "failed_windows": 0,
            }

            sw_note = f"  起始：第{args.start_window}个" if args.start_window > 1 else ""
            lw_note = f"  本次：{len(windows_to_run)}个" if (args.start_window > 1 or args.limit_windows > 0) else ""
            print(f"  消息：{len(messages)} 条  总切片：{total_windows} 个{sw_note}{lw_note}")

            if args.dry_run:
                for idx, (start, chunk) in enumerate(windows_to_run[:3]):
                    real_win_num = start_idx + idx + 1        # 1-based 全局编号
                    print(f"  窗口{real_win_num}（第{start+1}~{start+len(chunk)}条）：")
                    for m in chunk[:3]:
                        label   = "我" if m["role"] == "user" else contact_name
                        preview = m["content"][:50].replace("\n", " ")
                        print(f"    {m['timestamp']} [{label}] {preview}")
                    if len(chunk) > 3:
                        print(f"    ... 共{len(chunk)}条")
                if len(windows_to_run) > 3:
                    print(f"  （还有 {len(windows_to_run)-3} 个窗口未显示）")
                if args.start_window > 1 or args.limit_windows > 0:
                    print(f"  [范围] 总窗口：{total_windows}，起始：{args.start_window}，本次处理：{len(windows_to_run)}")
                print()
                log["files"].append(file_log)
                continue

            # 正式模式
            for win_idx, (start, chunk) in enumerate(windows_to_run):
                real_win_num = start_idx + win_idx + 1        # 1-based 全局编号
                fp_hash = window_fingerprint(chunk)
                if fp_hash in seen_fps:
                    continue
                seen_fps.add(fp_hash)

                window_text = format_window_for_prompt(chunk, contact_name)
                file_log["deepseek_calls"] += 1

                win_label = f"{real_win_num}/{total_windows}"
                print(f"  窗口 {win_label} ...", end=" ", flush=True)
                results = call_deepseek(window_text, contact_name, args.min_quality)

                if results is None:
                    print("失败")
                    file_log["failed_windows"] += 1
                    cnt["fail"] += 1
                    continue

                kept = 0
                for item in results:
                    score     = item.get("quality_score", 0)
                    case_type = item.get("case_type", "")
                    risk      = item.get("risk_level", "low")

                    if score < args.min_quality:
                        continue

                    # 安全校验：high risk 不能进 core
                    if case_type == "core_style" and risk == "high":
                        case_type = "restricted"
                        item["case_type"] = "restricted"
                        item["distill_eligible"] = False

                    # 后处理降级：关键词兜底，不依赖 DeepSeek 判断准确
                    if case_type == "core_style":
                        case_type = _downgrade_core(item, case_type)
                        item["case_type"] = case_type
                        if case_type != "core_style":
                            item["distill_eligible"] = False

                    # v4 后处理：字段一致性修正
                    item = _postprocess_item(item)
                    case_type = item["case_type"]

                    ts_tag    = datetime.now().strftime("%Y%m%d")
                    sample_id = f"{contact_name}_{ts_tag}_w{real_win_num}_{kept}"
                    case_obj  = make_case_obj(item, sample_id, contact_name, fp.name)
                    case_f.write(json.dumps(case_obj, ensure_ascii=False) + "\n")

                    if case_type == "core_style":
                        write_core_sample(core_f,   item, contact_name, sample_id)
                        write_core_sample(compat_f, item, contact_name, sample_id)
                        file_log["core_style"] += 1
                        cnt["core"] += 1
                        kept += 1

                    elif case_type == "advanced_style":
                        adv_f.write(json.dumps(case_obj, ensure_ascii=False) + "\n")
                        file_log["advanced_style"] += 1
                        cnt["advanced"] += 1
                        kept += 1

                    elif case_type == "restricted":
                        rest_f.write(json.dumps(case_obj, ensure_ascii=False) + "\n")
                        file_log["restricted"] += 1
                        cnt["restricted"] += 1
                        kept += 1

                    elif case_type == "negative":
                        write_negative_sample(neg_f, item, contact_name, sample_id)
                        file_log["negative"] += 1
                        cnt["negative"] += 1
                        kept += 1

                print(f"core={file_log['core_style']} adv={file_log['advanced_style']} "
                      f"rest={file_log['restricted']} neg={file_log['negative']} "
                      f"(本窗口+{kept})")

            log["files"].append(file_log)
            print(f"  文件完成：core={file_log['core_style']} advanced={file_log['advanced_style']} "
                  f"restricted={file_log['restricted']} negative={file_log['negative']}\n")

    finally:
        for f in [core_f, compat_f, adv_f, rest_f, neg_f, case_f]:
            if f:
                f.close()

    # 写日志
    log["summary"] = {
        "core_style_samples":   cnt["core"],
        "advanced_style_cases": cnt["advanced"],
        "restricted_cases":     cnt["restricted"],
        "negative_samples":     cnt["negative"],
        "failed_windows":       cnt["fail"],
        "total_case_cards":     cnt["core"] + cnt["advanced"] + cnt["restricted"] + cnt["negative"],
    }

    if not args.dry_run:
        (run_dir / "selection_log.json").write_text(
            json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")

        # 同步到 latest/（覆盖）
        if lat_dir.exists():
            shutil.rmtree(lat_dir)
        shutil.copytree(run_dir, lat_dir)

    # 汇总输出
    print(f"\n{'─'*64}")
    if args.dry_run:
        print("  DRY-RUN 完成，未调用 DeepSeek，未写入任何文件")
        print(f"\n  确认解析正确后运行：")
        print(f"    python scripts/select_developer_dialogue_samples.py --limit-files 2")
    else:
        total_cards = cnt["core"] + cnt["advanced"] + cnt["restricted"] + cnt["negative"]
        print(f"  筛选完成  run_id={run_id}")
        print(f"  core_style：{cnt['core']} 个")
        print(f"  advanced_style：{cnt['advanced']} 个")
        print(f"  restricted：{cnt['restricted']} 个")
        print(f"  negative：{cnt['negative']} 个")
        print(f"  case_cards 总计：{total_cards} 条")
        print(f"  失败窗口：{cnt['fail']} 个")
        print(f"\n  输出目录：{run_dir}")
        print(f"  latest 已更新：{lat_dir}")
        print(f"\n  ┌─ 后续操作说明 ──────────────────────────────────────")
        print(f"  │ core_style_samples.txt")
        print(f"  │   → 人工检查后可导入知识库「蒸馏专用」")
        print(f"  │ advanced_style_cases.jsonl")
        print(f"  │   → 进阶风格素材，默认不进入核心 DNA")
        print(f"  │ restricted_cases.jsonl")
        print(f"  │   → 高风险案例，仅做私有参考，默认不进入核心 DNA")
        print(f"  │ negative_samples.txt")
        print(f"  │   → 反面样本，不进入核心 DNA")
        print(f"  │")
        print(f"  │ ⚠ 请不要未经人工检查直接蒸馏")
        print(f"  └─────────────────────────────────────────────────────")
    print(f"{'─'*64}\n")


if __name__ == "__main__":
    main()
