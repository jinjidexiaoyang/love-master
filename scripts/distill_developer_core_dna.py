"""
scripts/distill_developer_core_dna.py
调用 Claude API，将精选样本蒸馏为开发者核心 DNA 文档。

用法：
  python scripts/distill_developer_core_dna.py --dry-run
  python scripts/distill_developer_core_dna.py
  python scripts/distill_developer_core_dna.py --max-core 150 --max-signature 60 --model claude-opus-4-5
"""

import sys
import os
import json
import yaml
import argparse
import textwrap
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from config import get_anthropic_config as _get_ac_cfg
_ac_cfg = _get_ac_cfg()
ANTHROPIC_API_KEY = _ac_cfg.api_key
DEFAULT_MODEL     = _ac_cfg.model

CURATED_DIR = ROOT / "data" / "developer" / "processed" / "final_dataset" / "curated"
OUT_DIR     = ROOT / "data" / "developer" / "processed" / "core_dna" / "v1_full"


# ─── 读取输入文件 ─────────────────────────────────────────────

def _load_jsonl(path: Path, limit: int = 0) -> list:
    if not path.exists():
        print(f"  [跳过] 文件不存在：{path.name}")
        return []
    items = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            items.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    if limit > 0:
        items = items[:limit]
    return items


# ─── 格式化样本为 prompt 片段 ─────────────────────────────────

def _fmt_dialogue(item: dict) -> str:
    contact = item.get("contact_name", "对方")
    lines = []
    for m in item.get("dialogue", []):
        label = "我" if m.get("role") == "user" else contact
        lines.append(f"  {label}：{m.get('content', '').strip()}")
    return "\n".join(lines)


def _fmt_core_block(items: list) -> str:
    parts = []
    for i, item in enumerate(items, 1):
        tags = "、".join(item.get("tags", [])[:4])
        mech = item.get("style_mechanism", "").strip()
        scene = item.get("scene", "")
        dialogue = _fmt_dialogue(item)
        parts.append(
            f"[Core #{i}] 场景={scene} 标签={tags}\n"
            f"  机制：{mech}\n"
            f"  对话：\n{dialogue}\n"
            f"  价值：{item.get('why_good','')[:80]}"
        )
    return "\n\n".join(parts)


def _fmt_signature_block(items: list) -> str:
    parts = []
    for i, item in enumerate(items, 1):
        tags = "、".join(item.get("tags", [])[:4])
        mech = item.get("style_mechanism", "").strip()
        dialogue = _fmt_dialogue(item)
        parts.append(
            f"[Signature #{i}] 标签={tags}\n"
            f"  机制：{mech}\n"
            f"  对话：\n{dialogue}"
        )
    return "\n\n".join(parts)


def _fmt_advanced_block(items: list) -> str:
    parts = []
    for i, item in enumerate(items, 1):
        usage = "；".join(item.get("usage_conditions", [])[:2])
        forbidden = "；".join(item.get("forbidden_contexts", [])[:2])
        dialogue = _fmt_dialogue(item)
        parts.append(
            f"[Advanced #{i}] 场景={item.get('scene','')} 阶段={item.get('stage','')}\n"
            f"  触发条件：{usage or '暧昧/熟人阶段'}\n"
            f"  禁用场景：{forbidden or '陌生人'}\n"
            f"  对话：\n{dialogue}"
        )
    return "\n\n".join(parts)


def _fmt_restricted_block(items: list) -> str:
    parts = []
    for i, item in enumerate(items, 1):
        parts.append(
            f"[Restricted Mechanism #{i}]\n"
            f"  机制名：{item.get('mechanism','')}\n"
            f"  风格特点：{item.get('raw_style','')[:60]}\n"
            f"  为什么有力：{item.get('why_powerful','')[:80]}\n"
            f"  适用前提：{'; '.join(item.get('usage_conditions',[])[:2])}\n"
            f"  禁用场景：{'; '.join(item.get('forbidden_contexts',[])[:2])}\n"
            f"  风险：{item.get('risk','')[:60]}\n"
            f"  低风险转译方向：{item.get('safe_translation','（待填写）')}"
        )
    return "\n\n".join(parts)


def _fmt_negative_block(items: list) -> str:
    parts = []
    for i, item in enumerate(items, 1):
        parts.append(
            f"[Negative #{i}]\n"
            f"  场景：{item.get('scene','')}\n"
            f"  问题：{item.get('why_bad','')[:100]}\n"
            f"  禁忌模式：{item.get('avoid_pattern','')}"
        )
    return "\n\n".join(parts)


# ─── Prompt 构造 ──────────────────────────────────────────────

SYSTEM = textwrap.dedent("""\
你是一个聊天风格 DNA 蒸馏专家。
你的任务是把开发者的真实聊天样本提炼成一套可供 AI 学习的风格 DNA 文档。

铁律：
1. 保留开发者的真实风格——短句、松弛、接梗、不解释、不低姿态、轻调侃、反差幽默、框架感、适度直球、情绪牵引、轻微锋利感。
2. 不要把风格净化成温柔安全的普通聊天助手。
3. restricted 机制只提炼原理，不把高风险原句变成默认回复规则。
4. 每个输出文件都要结构完整、可直接使用。
5. 只输出被要求的内容，不要额外解释或道歉。
""")


def _build_main_prompt(
    core_text: str,
    sig_text: str,
    adv_text: str,
    rest_text: str,
    neg_text: str,
) -> str:
    return textwrap.dedent(f"""\
请根据以下四层样本，蒸馏出完整的开发者核心 DNA 文档。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【第一层：核心风格样本（Core）】
这是主干，代表开发者最稳定、最可泛化的说话风格。

{core_text}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【第二层：辨识度样本（Signature）】
这些有更强的个人烙印，比 core 稍锋利或更有特色。
不要全部纳入默认风格，但要从中提炼辨识度特征。

{sig_text}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【第三层：进阶技巧（Advanced）】
这些有明确触发条件，适合在特定关系阶段使用。
在 DNA 里以"进阶技巧"呈现，必须带触发条件和禁用场景。

{adv_text}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【第四层：受限机制（Restricted）】
高风险，只提炼机制原理，不学习原句。
在 DNA 里作为"受限技巧"，需要明确标注风险和前提。

{rest_text}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【负面样本（Negative）】
明确不应该学的模式，直接转化为禁用规则。

{neg_text}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

请按以下顺序输出五个独立区块，每个区块用 ===BLOCK_START:文件名=== 和 ===BLOCK_END=== 包裹。

===BLOCK_START:developer_core_dna.md===
# Developer Core DNA

## 1. 一句话风格定义
（用一句话概括这个开发者的聊天风格 DNA，要有辨识度，不要模糊）

## 2. 底层心态
（3-6 条，描述这个人聊天时的内在心理状态和价值观）

## 3. 语言风格
（具体的语言特征：句式偏好、用词习惯、标点/语气词使用、是否有口头禅等）

## 4. 对话节奏
（节奏控制：什么时候快、什么时候慢、怎么开场、怎么收尾、如何转换话题）

## 5. 核心回复机制
（列出最常见、最典型的回复策略，每条给1-2个使用示例）

## 6. 张力与情绪牵引
（如何制造张力、牵引对方情绪，有哪些标志性操作）

## 7. 框架感与边界
（如何保持自己的框架、如何处理对方试探/追问/讨好）

## 8. 进阶技巧
（来自 advanced 层，每条必须标注：触发条件 / 禁用场景 / 效果）

## 9. 受限技巧
（来自 restricted 层，每条必须标注：机制 / 风险 / 适用前提 / 低风险替代方式）

## 10. 禁用/高风险模式
（来自 negative 层，列出明确禁止的聊天行为和原因）

## 11. 场景化使用建议
（针对不同关系阶段和场景的使用建议，如：陌生人/认识/暧昧阶段分别怎么用这套 DNA）

## 12. 用户侧回复生成原则
（给 AI 生成回复时用的原则，格式为：当X时，优先Y，避免Z）
===BLOCK_END===

===BLOCK_START:style_profile.json===
{{
  "dna_version": "v1",
  "created_at": "{datetime.now().isoformat()}",
  "one_line_definition": "",
  "core_traits": [],
  "language_patterns": {{
    "sentence_style": [],
    "preferred_length": "",
    "tone": [],
    "markers": []
  }},
  "rhythm": {{
    "pacing": "",
    "opening_style": "",
    "closing_style": "",
    "topic_switch": ""
  }},
  "key_mechanisms": [],
  "tension_tools": [],
  "frame_rules": [],
  "advanced_techniques": [],
  "restricted_techniques": [],
  "forbidden_patterns": [],
  "stage_guidance": {{
    "stranger": "",
    "acquaintance": "",
    "ambiguous": ""
  }}
}}
===BLOCK_END===

===BLOCK_START:developer_style_rules.jsonl===
（每行一条 JSON 规则，格式：）
{{"rule_id": "R001", "category": "core|advanced|restricted|forbidden", "trigger": "触发条件", "action": "执行动作", "avoid": "避免什么", "example": "示例", "risk": "风险等级 low|medium|high"}}
（输出 30-50 条规则，覆盖 core/advanced/restricted/forbidden 各类别）
===BLOCK_END===

===BLOCK_START:reply_principles.yaml===
# Developer Reply Principles
# 用于指导 AI 生成回复时的核心原则

version: "1.0"
core_principles:
  # 列出 8-12 条核心原则，每条包含 when/do/avoid

advanced_principles:
  # 列出 6-10 条进阶原则，每条包含 trigger/action/forbidden_context

restricted_awareness:
  # 列出 4-8 条受限技巧的机制摘要

forbidden_rules:
  # 列出 6-10 条明确禁止规则
===BLOCK_END===

===BLOCK_START:distillation_notes===
（3-5 条蒸馏备注：数据质量评估、风格提炼置信度、建议后续补充的样本类型、其他注意事项）
===BLOCK_END===
""")


# ─── Claude API 调用 ──────────────────────────────────────────

def _call_claude(prompt: str, model: str) -> tuple[str, dict]:
    """调用 Claude，返回 (response_text, usage_dict)"""
    import anthropic
    from config import get_anthropic_config as _get_ac
    _ac = _get_ac()
    kwargs = {"api_key": _ac.api_key}
    if _ac.base_url:
        kwargs["base_url"] = _ac.base_url
    client = anthropic.Anthropic(**kwargs)
    response = client.messages.create(
        model=model,
        max_tokens=8000,
        system=SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    text  = response.content[0].text
    usage = {
        "input_tokens":  response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
    }
    return text, usage


# ─── 解析 Claude 输出 ─────────────────────────────────────────

def _extract_blocks(raw: str) -> dict[str, str]:
    """从 Claude 输出中提取各 BLOCK 内容"""
    blocks = {}
    import re
    pattern = re.compile(
        r"===BLOCK_START:([^=]+)===\n(.*?)===BLOCK_END===",
        re.DOTALL
    )
    for m in pattern.finditer(raw):
        name    = m.group(1).strip()
        content = m.group(2).strip()
        blocks[name] = content
    return blocks


def _fix_style_profile(raw_json: str, model: str, created_at: str) -> str:
    """尝试修复 style_profile.json，确保是合法 JSON"""
    # 替换模板里的占位日期
    raw_json = raw_json.replace(f'"created_at": "{datetime.now().isoformat()}"',
                                f'"created_at": "{created_at}"')
    try:
        obj = json.loads(raw_json)
        obj["created_at"] = created_at
        obj["dna_version"] = "v1"
        obj["distilled_by"] = model
        return json.dumps(obj, ensure_ascii=False, indent=2)
    except json.JSONDecodeError:
        # 解析失败时包一层错误信息，不阻断流程
        return json.dumps({
            "dna_version": "v1",
            "created_at": created_at,
            "distilled_by": model,
            "error": "Claude 输出的 JSON 解析失败，请手动检查",
            "raw": raw_json[:2000],
        }, ensure_ascii=False, indent=2)


def _fix_rules_jsonl(raw: str) -> str:
    """提取 jsonl block 中合法的 JSON 行"""
    valid_lines = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("（") or line.startswith("#"):
            continue
        try:
            json.loads(line)
            valid_lines.append(line)
        except json.JSONDecodeError:
            pass
    return "\n".join(valid_lines)


# ─── 主流程 ───────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="蒸馏开发者核心 DNA（调用 Claude）")
    parser.add_argument("--input-dir",      type=str, default="")
    parser.add_argument("--output-dir",     type=str, default="")
    parser.add_argument("--model",          type=str, default=DEFAULT_MODEL)
    parser.add_argument("--max-core",       type=int, default=150)
    parser.add_argument("--max-signature",  type=int, default=60)
    parser.add_argument("--max-advanced",   type=int, default=60)
    parser.add_argument("--max-restricted", type=int, default=40)
    parser.add_argument("--max-negative",   type=int, default=20)
    parser.add_argument("--dry-run",        action="store_true")
    args = parser.parse_args()

    in_dir  = Path(args.input_dir)  if args.input_dir  else CURATED_DIR
    out_dir = Path(args.output_dir) if args.output_dir else OUT_DIR

    # ── 读取输入 ──────────────────────────────────────────────
    core_items = _load_jsonl(in_dir / "final_core_dna_samples.jsonl",     args.max_core)
    sig_items  = _load_jsonl(in_dir / "final_signature_style_cases.jsonl",args.max_signature)
    adv_items  = _load_jsonl(in_dir / "final_advanced_style_cases.jsonl", args.max_advanced)
    rest_items = _load_jsonl(in_dir / "final_restricted_mechanisms.jsonl",args.max_restricted)
    neg_items  = _load_jsonl(in_dir / "final_negative_rules.jsonl",       args.max_negative)

    print(f"\n{'='*64}")
    print(f"  开发者核心 DNA 蒸馏{'  [DRY-RUN]' if args.dry_run else ''}")
    print(f"  模型：{args.model}")
    print(f"  输入目录：{in_dir}")
    print(f"{'='*64}")
    print(f"  将使用样本数量：")
    print(f"    core_style：      {len(core_items)} 条（上限 {args.max_core}）")
    print(f"    signature_style： {len(sig_items)} 条（上限 {args.max_signature}）")
    print(f"    advanced_style：  {len(adv_items)} 条（上限 {args.max_advanced}）")
    print(f"    restricted：      {len(rest_items)} 条（上限 {args.max_restricted}）")
    print(f"    negative：        {len(neg_items)} 条（上限 {args.max_negative}）")
    print(f"  输出目录：{out_dir}")
    print(f"{'='*64}\n")

    if args.dry_run:
        print("DRY-RUN 完成，未调用 Claude，未写入任何文件\n")
        sys.exit(0)

    if not ANTHROPIC_API_KEY:
        print("[错误] 未找到 ANTHROPIC_API_KEY，请在 .env 中配置\n")
        sys.exit(1)

    if not core_items:
        print("[错误] core 样本为空，请先运行 curate_final_dna_samples.py\n")
        sys.exit(1)

    # ── 构造 prompt ───────────────────────────────────────────
    print("构造 prompt ...", end=" ", flush=True)
    core_text = _fmt_core_block(core_items)
    sig_text  = _fmt_signature_block(sig_items)  if sig_items  else "（无辨识度样本）"
    adv_text  = _fmt_advanced_block(adv_items)   if adv_items  else "（无进阶样本）"
    rest_text = _fmt_restricted_block(rest_items) if rest_items else "（无受限机制）"
    neg_text  = _fmt_negative_block(neg_items)   if neg_items  else "（无负面样本）"
    prompt    = _build_main_prompt(core_text, sig_text, adv_text, rest_text, neg_text)
    print(f"完成  prompt 长度：{len(prompt)} 字符")

    # ── 调用 Claude ───────────────────────────────────────────
    print(f"调用 Claude ({args.model}) ...", end=" ", flush=True)
    created_at = datetime.now().isoformat()
    log = {
        "model":       args.model,
        "created_at":  created_at,
        "input_counts": {
            "core":       len(core_items),
            "signature":  len(sig_items),
            "advanced":   len(adv_items),
            "restricted": len(rest_items),
            "negative":   len(neg_items),
        },
        "output_files": [],
        "usage":        {},
        "success":      False,
        "error":        "",
    }

    try:
        raw_output, usage = _call_claude(prompt, args.model)
        log["usage"]   = usage
        log["success"] = True
        print(f"完成  tokens: in={usage['input_tokens']} out={usage['output_tokens']}")
    except Exception as e:
        log["error"] = str(e)
        print(f"\n[错误] Claude 调用失败：{e}\n")
        # 写失败日志后退出
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "distillation_log.json").write_text(
            json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")
        sys.exit(1)

    # ── 解析输出 ──────────────────────────────────────────────
    print("解析输出 ...", end=" ", flush=True)
    blocks = _extract_blocks(raw_output)
    print(f"提取到 {len(blocks)} 个 block：{list(blocks.keys())}")

    # ── 写文件 ────────────────────────────────────────────────
    if out_dir.exists():
        print(f"[提示] 输出目录已存在，将覆盖：{out_dir}")
    out_dir.mkdir(parents=True, exist_ok=True)

    written: list[str] = []

    # 1. developer_core_dna.md
    md_content = blocks.get("developer_core_dna.md", "")
    if not md_content:
        md_content = f"# Developer Core DNA\n\n（Claude 未返回完整内容，请检查 raw_output.txt）\n"
    (out_dir / "developer_core_dna.md").write_text(md_content, encoding="utf-8")
    written.append("developer_core_dna.md")

    # 2. style_profile.json
    raw_profile = blocks.get("style_profile.json", "{}")
    profile_json = _fix_style_profile(raw_profile, args.model, created_at)
    (out_dir / "style_profile.json").write_text(profile_json, encoding="utf-8")
    written.append("style_profile.json")

    # 3. developer_style_rules.jsonl
    raw_rules = blocks.get("developer_style_rules.jsonl", "")
    rules_jsonl = _fix_rules_jsonl(raw_rules)
    (out_dir / "developer_style_rules.jsonl").write_text(rules_jsonl, encoding="utf-8")
    rule_count = len([l for l in rules_jsonl.splitlines() if l.strip()])
    written.append(f"developer_style_rules.jsonl ({rule_count} 条规则)")

    # 4. reply_principles.yaml
    raw_yaml = blocks.get("reply_principles.yaml", "")
    if not raw_yaml:
        raw_yaml = "# reply_principles.yaml\n# Claude 未返回内容，请检查 raw_output.txt\n"
    # 验证 YAML 合法性，不合法则包裹为注释
    try:
        yaml.safe_load(raw_yaml)
        yaml_content = raw_yaml
    except yaml.YAMLError:
        yaml_content = (
            "# [警告] YAML 解析失败，以下为原始输出，请手动修正\n"
            + "\n".join(f"# {line}" for line in raw_yaml.splitlines())
        )
    (out_dir / "reply_principles.yaml").write_text(yaml_content, encoding="utf-8")
    written.append("reply_principles.yaml")

    # 5. 备份完整原始输出（便于排查）
    (out_dir / "raw_claude_output.txt").write_text(raw_output, encoding="utf-8")
    written.append("raw_claude_output.txt")

    # 6. distillation_log.json
    log["output_files"] = written
    notes_raw = blocks.get("distillation_notes", "")
    log["distillation_notes"] = [l.strip("- ·•　 ").strip()
                                  for l in notes_raw.splitlines()
                                  if l.strip() and not l.strip().startswith("#")]
    (out_dir / "distillation_log.json").write_text(
        json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")
    written.append("distillation_log.json")

    # ── 汇总输出 ──────────────────────────────────────────────
    print(f"\n{'─'*64}")
    print(f"  蒸馏完成")
    print(f"  输出文件：")
    for f in written:
        print(f"    {f}")
    print(f"  输出目录：{out_dir}")
    print(f"  Token 消耗：input={log['usage'].get('input_tokens',0)}  "
          f"output={log['usage'].get('output_tokens',0)}")
    print(f"{'─'*64}\n")


if __name__ == "__main__":
    main()
