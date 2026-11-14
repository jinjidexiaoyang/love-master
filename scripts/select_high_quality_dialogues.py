"""
scripts/select_high_quality_dialogues.py — 聊天记录高质量片段筛选脚本

流程：
  data/raw_chats/*.txt
    → 解析时间戳格式消息
    → 按窗口切片（默认30条，重叠5条）
    → DeepSeek API 筛选（dry-run 模式跳过）
    → 输出到 data/processed/selected_dialogues/

用法：
  # 预览（不调用 DeepSeek）
  python scripts/select_high_quality_dialogues.py --dry-run

  # 只测试前2个文件
  python scripts/select_high_quality_dialogues.py --limit-files 2

  # 正式筛选
  python scripts/select_high_quality_dialogues.py

  # 自定义参数
  python scripts/select_high_quality_dialogues.py --window-size 40 --overlap 8 --min-quality 7
"""

import sys
import os
import re
import json
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

# ─── 目录配置 ─────────────────────────────────────────────────
RAW_DIR = ROOT / "data" / "raw_chats"
OUT_DIR = ROOT / "data" / "processed" / "selected_dialogues"

for d in [RAW_DIR, OUT_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# ─── DeepSeek 配置 ────────────────────────────────────────────
DEEPSEEK_API_KEY  = _ds_cfg.api_key
DEEPSEEK_MODEL    = _ds_cfg.model
DEEPSEEK_BASE_URL = _ds_cfg.base_url

# ─── 时间戳格式解析 ───────────────────────────────────────────
_TS_HEADER = re.compile(r"^(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})\s+'(.+?)'$")
_VOICE_PREFIX = re.compile(r"^\[语音转文字\]\s*")


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
    """
    解析时间戳格式聊天记录，返回：
    [{"role": "user"/"other", "speaker": str, "content": str, "timestamp": str}]
    """
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
        ts = m.group(1)
        speaker = m.group(2).strip()
        content_lines = [l.strip() for l in lines[1:] if l.strip()]
        content = " ".join(content_lines)
        content = _VOICE_PREFIX.sub("", content).strip()
        if not content:
            continue
        role = "user" if speaker == "我" else "other"
        messages.append({"role": role, "speaker": speaker, "content": content, "timestamp": ts})
    return messages


def make_windows(messages: list, window_size: int, overlap: int) -> list:
    """按窗口切片，返回 [(start_idx, msgs), ...]"""
    windows = []
    step = max(1, window_size - overlap)
    i = 0
    while i < len(messages):
        chunk = messages[i:i + window_size]
        if len(chunk) >= 5:  # 少于5条的窗口跳过
            windows.append((i, chunk))
        i += step
    return windows


def window_fingerprint(msgs: list) -> str:
    """用消息内容生成去重指纹"""
    raw = "".join(m["content"] for m in msgs)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def format_window_for_prompt(msgs: list, contact_name: str) -> str:
    lines = []
    for m in msgs:
        label = "我" if m["role"] == "user" else contact_name
        lines.append(f"{m['timestamp']} [{label}]: {m['content']}")
    return "\n".join(lines)


# ─── DeepSeek Prompt ──────────────────────────────────────────
SYSTEM_PROMPT = """你是一个聊天记录质量评估专家。
你的任务是从给定的聊天片段中，识别出有价值的对话片段，用于帮助用户学习自己的聊天风格。

重要规则：
1. 绝对不得编造任何内容
2. 绝对不得改写原始对话，必须保留原话
3. 如果没有高价值片段，返回空数组
4. 只输出 JSON，不要任何额外文字

评估维度：
A. 蒸馏专用（style_sample）— 适合学习"我"的说话风格：
   - 我方回复自然、有个性、不低姿态
   - 短句、轻调侃、稳定、有边界感
   - 对方后续反应积极（继续聊、追问、笑）
   - 能体现独特的表达方式或节奏

B. 案例库（case）— 有完整策略价值：
   - 有明确的触发点（她说了什么）
   - 有我的关键回复
   - 有可见的结果反馈（她的反应）
   - 有可复用的策略逻辑

C. 负面样本（negative）— 不建议学习：
   - 我方连续追问、催回复
   - 低姿态讨好、过度解释
   - 情绪化输出（发脾气、道歉连篇）
   - 对方明显冷淡或终结话题后我还在硬聊"""

def build_user_prompt(window_text: str, contact_name: str) -> str:
    return f"""请分析以下聊天片段（联系人：{contact_name}），识别其中有价值的对话片段。

【聊天片段】
{window_text}

请输出 JSON 数组，每个元素格式如下：
{{
  "type": "style_sample" | "case" | "negative",
  "quality_score": 1-10的整数,
  "scene": "场景描述（10字内）",
  "stage": "陌生人/认识/熟人/暧昧/上头/痴迷",
  "dialogue": [
    {{"role": "user", "content": "原始消息，不得改写"}},
    {{"role": "other", "content": "原始消息，不得改写"}}
  ],
  "why_good": "为什么值得学习（style_sample/case）或为什么是负面样本（negative）",
  "tags": ["标签1", "标签2"],
  "risk": "使用风险或注意事项（没有则填空字符串）"
}}

注意：
- dialogue 只包含该片段的核心消息，不要把整个窗口都放进去
- 如果没有任何有价值的片段，返回空数组 []
- quality_score 低于 6 的片段不需要返回"""


# ─── DeepSeek API 调用 ────────────────────────────────────────
def call_deepseek(window_text: str, contact_name: str) -> list:
    """调用 DeepSeek，返回筛选结果列表，失败返回 None"""
    try:
        from openai import OpenAI
        client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)
        response = client.chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": build_user_prompt(window_text, contact_name)},
            ],
            temperature=0.2,
            max_tokens=2000,
        )
        raw = response.choices[0].message.content.strip()
        # 清理 markdown 代码块
        if "```json" in raw:
            raw = raw.split("```json")[1].split("```")[0].strip()
        elif "```" in raw:
            raw = raw.split("```")[1].split("```")[0].strip()
        result = json.loads(raw)
        if isinstance(result, list):
            return result
        return []
    except json.JSONDecodeError:
        return None  # 解析失败
    except Exception as e:
        print(f"    [DeepSeek 错误] {e}")
        return None


# ─── 输出写入 ─────────────────────────────────────────────────
def write_style_sample(f, item: dict, contact_name: str, sample_id: str):
    dialogue_lines = []
    for msg in item.get("dialogue", []):
        label = "我" if msg["role"] == "user" else contact_name
        dialogue_lines.append(f"{label}：{msg['content']}")

    f.write(f"""【样本ID】{sample_id}
【联系人】{contact_name}
【场景】{item.get('scene', '')}
【关系阶段】{item.get('stage', '')}
【原始对话】
{chr(10).join(dialogue_lines)}

【为什么值得学习】
{item.get('why_good', '')}

【可学习点】
{chr(10).join('- ' + t for t in item.get('tags', []))}

【风险】
{item.get('risk', '无')}

{'─' * 60}

""")


def write_negative_sample(f, item: dict, contact_name: str, sample_id: str):
    dialogue_lines = []
    for msg in item.get("dialogue", []):
        label = "我" if msg["role"] == "user" else contact_name
        dialogue_lines.append(f"{label}：{msg['content']}")

    f.write(f"""【负面样本ID】{sample_id}
【联系人】{contact_name}
【场景】{item.get('scene', '')}
【原始对话】
{chr(10).join(dialogue_lines)}

【为什么是负面样本】
{item.get('why_good', '')}

{'─' * 60}

""")


# ─── 主流程 ───────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="聊天记录高质量片段筛选")
    parser.add_argument("--dry-run", action="store_true", help="只解析切片，不调用 DeepSeek")
    parser.add_argument("--limit-files", type=int, default=0, help="只处理前N个文件（0=全部）")
    parser.add_argument("--window-size", type=int, default=30, help="每个窗口的消息条数（默认30）")
    parser.add_argument("--overlap", type=int, default=5, help="窗口重叠条数（默认5）")
    parser.add_argument("--min-quality", type=int, default=8, help="最低质量分（默认8）")
    args = parser.parse_args()

    if not args.dry_run and not DEEPSEEK_API_KEY:
        print("[错误] 未找到 DEEPSEEK_API_KEY，请在 .env 中配置，或使用 --dry-run 模式")
        sys.exit(1)

    txt_files = sorted(RAW_DIR.glob("*.txt"))
    if not txt_files:
        print(f"[提示] {RAW_DIR} 下没有 .txt 文件，请把原始聊天记录放进去")
        sys.exit(0)

    if args.limit_files > 0:
        txt_files = txt_files[:args.limit_files]

    mode = "[DRY-RUN]" if args.dry_run else "[正式筛选]"
    print(f"\n{'='*60}")
    print(f"  聊天记录高质量片段筛选  {mode}")
    print(f"  文件数：{len(txt_files)}  窗口：{args.window_size}条  重叠：{args.overlap}条  最低分：{args.min_quality}")
    print(f"{'='*60}\n")

    # 输出文件句柄
    style_path   = OUT_DIR / "style_samples_high_quality.txt"
    case_path    = OUT_DIR / "case_cards.jsonl"
    neg_path     = OUT_DIR / "negative_samples.txt"
    log_path     = OUT_DIR / "selection_log.json"

    style_f = open(style_path, "a", encoding="utf-8")
    case_f  = open(case_path, "a", encoding="utf-8")
    neg_f   = open(neg_path, "a", encoding="utf-8")

    log = {
        "run_time": datetime.now().isoformat(),
        "mode": "dry_run" if args.dry_run else "full",
        "files": []
    }

    seen_fingerprints = set()
    total_style = total_case = total_neg = total_fail = 0

    for fp in txt_files:
        contact_name = extract_name_from_filename(fp.name)
        print(f"[处理] {fp.name}  →  联系人：{contact_name}")

        try:
            text = fp.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = fp.read_text(encoding="gbk")

        messages = parse_messages(text)
        windows  = make_windows(messages, args.window_size, args.overlap)

        file_log = {
            "file": fp.name,
            "contact": contact_name,
            "total_messages": len(messages),
            "total_windows": len(windows),
            "deepseek_calls": 0,
            "style_samples": 0,
            "cases": 0,
            "negatives": 0,
            "failed_windows": 0,
        }

        print(f"  消息：{len(messages)} 条  切片：{len(windows)} 个")

        if args.dry_run:
            # dry-run：只打印前3个窗口预览
            for idx, (start, chunk) in enumerate(windows[:3]):
                print(f"  窗口{idx+1}（第{start+1}~{start+len(chunk)}条）：")
                for m in chunk[:3]:
                    label = "我" if m["role"] == "user" else contact_name
                    print(f"    {m['timestamp']} [{label}] {m['content'][:40]}")
                if len(chunk) > 3:
                    print(f"    ... 共{len(chunk)}条")
            if len(windows) > 3:
                print(f"  （还有 {len(windows)-3} 个窗口未显示）")
            log["files"].append(file_log)
            continue

        # 正式模式：调用 DeepSeek
        for win_idx, (start, chunk) in enumerate(windows):
            fp_hash = window_fingerprint(chunk)
            if fp_hash in seen_fingerprints:
                continue
            seen_fingerprints.add(fp_hash)

            window_text = format_window_for_prompt(chunk, contact_name)
            file_log["deepseek_calls"] += 1

            print(f"  窗口 {win_idx+1}/{len(windows)}...", end=" ", flush=True)
            results = call_deepseek(window_text, contact_name)

            if results is None:
                print("失败")
                file_log["failed_windows"] += 1
                total_fail += 1
                continue

            kept = 0
            for item in results:
                score = item.get("quality_score", 0)
                if score < args.min_quality:
                    continue

                item_type = item.get("type", "")
                sample_id = f"{contact_name}_{datetime.now().strftime('%Y%m%d')}_{win_idx}_{kept}"

                if item_type == "style_sample":
                    write_style_sample(style_f, item, contact_name, sample_id)
                    # 同时写入 case_cards（style_sample 也有案例价值）
                    case_obj = {
                        "case_id": sample_id,
                        "contact_name": contact_name,
                        "source_file": fp.name,
                        "scene": item.get("scene", ""),
                        "stage": item.get("stage", ""),
                        "dialogue": item.get("dialogue", []),
                        "why_good": item.get("why_good", ""),
                        "tags": item.get("tags", []),
                        "risk": item.get("risk", ""),
                        "quality_score": score,
                        "type": "style_sample",
                    }
                    case_f.write(json.dumps(case_obj, ensure_ascii=False) + "\n")
                    file_log["style_samples"] += 1
                    total_style += 1
                    kept += 1

                elif item_type == "case":
                    case_obj = {
                        "case_id": sample_id,
                        "contact_name": contact_name,
                        "source_file": fp.name,
                        "scene": item.get("scene", ""),
                        "stage": item.get("stage", ""),
                        "dialogue": item.get("dialogue", []),
                        "why_good": item.get("why_good", ""),
                        "tags": item.get("tags", []),
                        "risk": item.get("risk", ""),
                        "quality_score": score,
                        "type": "case",
                    }
                    case_f.write(json.dumps(case_obj, ensure_ascii=False) + "\n")
                    file_log["cases"] += 1
                    total_case += 1
                    kept += 1

                elif item_type == "negative":
                    write_negative_sample(neg_f, item, contact_name, sample_id)
                    file_log["negatives"] += 1
                    total_neg += 1
                    kept += 1

            print(f"选中 {kept} 个片段")

        log["files"].append(file_log)
        print(f"  文件完成：蒸馏样本 {file_log['style_samples']} / 案例 {file_log['cases']} / 负面 {file_log['negatives']}\n")

    style_f.close()
    case_f.close()
    neg_f.close()

    # 写日志
    log["summary"] = {
        "total_style_samples": total_style,
        "total_cases": total_case,
        "total_negatives": total_neg,
        "total_failed_windows": total_fail,
    }
    log_path.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n{'─'*60}")
    if args.dry_run:
        print("  DRY-RUN 完成，未调用 DeepSeek，未写入任何输出文件")
        print(f"  确认无误后，运行正式筛选：")
        print(f"    python scripts/select_high_quality_dialogues.py --limit-files 2")
    else:
        print(f"  筛选完成")
        print(f"  蒸馏专用样本：{total_style} 个  →  {style_path}")
        print(f"  案例卡：{total_style + total_case} 条  →  {case_path}")
        print(f"  负面样本：{total_neg} 个  →  {neg_path}")
        print(f"  失败窗口：{total_fail} 个")
        print(f"  日志：{log_path}")
    print(f"{'─'*60}\n")


if __name__ == "__main__":
    main()
