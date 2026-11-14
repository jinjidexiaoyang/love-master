"""
scripts/build_runtime_style_context.py
从 v1_full DNA 生成轻量运行时版本 v1_runtime。
不调用任何 API，纯本地处理。

用法：
  python scripts/build_runtime_style_context.py
  python scripts/build_runtime_style_context.py --dry-run
  python scripts/build_runtime_style_context.py --input-dir data/developer/processed/core_dna/v1_full
"""

import json
import re
import argparse
from datetime import datetime
from pathlib import Path

ROOT     = Path(__file__).resolve().parent.parent
IN_DIR   = ROOT / "data" / "developer" / "processed" / "core_dna" / "v1_full"
OUT_DIR  = ROOT / "data" / "developer" / "processed" / "core_dna" / "v1_runtime"

# 阶段关键词 → stage key 映射
_STAGE_KEYS = {
    "stranger":     ["stranger", "陌生人"],
    "acquaintance": ["acquaintance", "认识"],
    "familiar":     ["familiar", "熟人"],
    "ambiguous":    ["ambiguous", "暧昧"],
    "intimate":     ["intimate", "亲密"],
    "conflict":     ["conflict", "冲突"],
    "cooling_down": ["cooling_down", "冷却"],
    "rejection":    ["rejection", "被拒"],
}


# ─── 读取工具 ─────────────────────────────────────────────────

def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        print(f"  [警告] 未找到：{path.name}")
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
    return items


def load_json(path: Path) -> dict:
    if not path.exists():
        print(f"  [警告] 未找到：{path.name}")
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"  [警告] {path.name} 解析失败：{e}")
        return {}


def load_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


# ─── 规则过滤 ─────────────────────────────────────────────────

def filter_runtime_rules(rules: list[dict]) -> list[dict]:
    """runtime_enabled=true，且 rule_type 在核心类型中"""
    core_types = {"mindset", "language", "rhythm", "frame", "core_pattern", "signature"}
    return [
        r for r in rules
        if r.get("runtime_enabled", True)
        and r.get("rule_type", "") in core_types
    ]


def filter_negative_rules(rules: list[dict]) -> list[dict]:
    return [r for r in rules if r.get("rule_type") == "negative"]


def filter_restricted_rules(rules: list[dict]) -> list[dict]:
    return [r for r in rules if r.get("rule_type") == "restricted"]


def filter_stage_rules(rules: list[dict]) -> list[dict]:
    return [r for r in rules if r.get("rule_type") == "stage_usage"]


def _slim_rule(r: dict) -> dict:
    return {
        "rule_id":   r.get("rule_id", ""),
        "rule_type": r.get("rule_type", ""),
        "title":     r.get("title", ""),
        "content":   r.get("content", ""),
        "priority":  r.get("priority", 1),
        "examples":  r.get("examples", []),
    }


# ─── stage 分桶 ───────────────────────────────────────────────

def bucket_stage_rules(stage_rules: list[dict]) -> dict:
    buckets: dict[str, list] = {k: [] for k in _STAGE_KEYS}
    for r in stage_rules:
        title   = r.get("title", "").lower()
        content = r.get("content", "").lower()
        conds   = " ".join(r.get("usage_conditions", [])).lower()
        matched = False
        for stage_key, keywords in _STAGE_KEYS.items():
            for kw in keywords:
                if kw in title or kw in conds or kw in content:
                    buckets[stage_key].append(_slim_rule(r))
                    matched = True
                    break
            if matched:
                break
        if not matched:
            buckets["stranger"].append(_slim_rule(r))
    return buckets


# ─── runtime_style_profile.json ──────────────────────────────

def build_runtime_profile(profile: dict) -> dict:
    return {
        "version":          "v1_runtime",
        "one_line_definition": profile.get("one_line_definition", ""),
        "core_traits":      profile.get("core_traits", []),
        "language_patterns": profile.get("language_patterns", {}),
        "key_mechanisms":   profile.get("key_mechanisms", []),
        "tension_tools":    profile.get("tension_tools", []),
        "frame_rules":      profile.get("frame_rules", []),
        "runtime_notes": [
            "本文件为运行时轻量版，完整 DNA 见 v1_full/style_profile.json",
            "advanced/restricted 规则不默认使用，需根据关系阶段显式调用",
            "保留风格张力，不过度安全化",
        ],
    }


# ─── runtime_style_prompt.md ─────────────────────────────────

def build_runtime_prompt(
    profile: dict,
    runtime_rules: list[dict],
    negative_rules: list[dict],
    restricted_rules: list[dict],
    max_chars: int,
) -> str:

    one_line = profile.get("one_line_definition", "松弛有框架、接梗不低姿态、轻调侃带张力的高价值聊天风格")
    core_traits = profile.get("core_traits", [])
    lp = profile.get("language_patterns", {})
    key_mechs = profile.get("key_mechanisms", [])
    tension = profile.get("tension_tools", [])
    frame_rules = profile.get("frame_rules", [])
    rhythm = profile.get("rhythm", {})

    # 按 rule_type 分组 runtime_rules
    by_type: dict[str, list] = {}
    for r in runtime_rules:
        rt = r.get("rule_type", "other")
        by_type.setdefault(rt, []).append(r)

    def _rules_block(rtype: str, limit: int = 6) -> str:
        items = by_type.get(rtype, [])[:limit]
        if not items:
            return ""
        lines = []
        for r in items:
            ex = r.get("examples", [])
            ex_str = f"  示例：{' / '.join(ex[:2])}" if ex else ""
            lines.append(f"- {r['title']}：{r['content'][:80]}{ex_str}")
        return "\n".join(lines)

    # 负面规则摘要
    neg_lines = []
    for r in negative_rules[:10]:
        neg_lines.append(f"- {r.get('title','')}")

    # restricted 边界摘要
    rest_lines = []
    for r in restricted_rules[:6]:
        title = r.get("title", "")
        content = r.get("content", "")
        # 只取机制和风险部分
        mech = ""
        risk = ""
        for seg in content.split("。"):
            if "机制" in seg:
                mech = seg.replace("【机制】", "").strip()
            if "风险" in seg:
                risk = seg.replace("【风险】", "").strip()
        rest_lines.append(f"- {title}：机制={mech[:30]}；风险={risk[:30]}")

    # 语言风格摘要
    sentence_styles = lp.get("sentence_style", [])
    tones = lp.get("tone", [])
    markers = lp.get("markers", [])
    preferred_len = lp.get("preferred_length", "")

    # 节奏摘要
    pacing = rhythm.get("pacing", "")
    opening = rhythm.get("opening_style", "")
    closing = rhythm.get("closing_style", "")

    # 核心机制摘要（取前6条）
    mech_lines = []
    for m in key_mechs[:6]:
        if "：" in m:
            name, desc = m.split("：", 1)
            mech_lines.append(f"- **{name.strip()}**：{desc.strip()[:60]}")
        else:
            mech_lines.append(f"- {m[:60]}")

    # signature 工具
    tension_clean = [t.replace("（", "（示例：") for t in tension[:6]]

    # frame 规则
    frame_lines = [f"- {f}" for f in frame_rules[:6]]

    # advanced 原则
    adv_rules = by_type.get("advanced", [])
    adv_lines = []
    for r in adv_rules[:5]:
        conds = "；".join(r.get("usage_conditions", [])[:2])
        forbidden = "；".join(r.get("forbidden_contexts", [])[:2])
        adv_lines.append(
            f"- **{r['title']}**：触发条件={conds or '对方有正向反馈'}；"
            f"禁用={forbidden or '关系初期'}"
        )

    prompt = f"""# 开发者风格 DNA — 运行时上下文

> 本文件为 AI 回复生成时注入的风格上下文。
> 这不是普通温柔安全助手的风格，而是有框架感、松弛感、轻调侃、适度张力的真实聊天风格。

---

## 1. 一句话风格定义

{one_line}

---

## 2. 默认心态

{"".join(f"- {t}{chr(10)}" for t in core_traits[:6]).rstrip()}

---

## 3. 语言风格

**句式偏好**：
{"".join(f"- {s}{chr(10)}" for s in sentence_styles[:4]).rstrip()}

**语气**：{" / ".join(tones[:5])}

**标志性用词**：{" / ".join(markers[:6])}

**消息长度**：{preferred_len}

---

## 4. 对话节奏

- 节奏控制：{pacing}
- 开场：{opening}
- 收尾：{closing}

{_rules_block("rhythm", 4)}

---

## 5. 核心回复机制

{"".join(f"{l}{chr(10)}" for l in mech_lines).rstrip()}

{_rules_block("core_pattern", 6)}

---

## 6. 框架感与边界

{"".join(f"{l}{chr(10)}" for l in frame_lines).rstrip()}

{_rules_block("frame", 4)}

---

## 7. Signature 风格增强

以下是高辨识度风格工具，用于制造张力和个人烙印：

{"".join(f"- {t}{chr(10)}" for t in tension_clean).rstrip()}

{_rules_block("signature", 5)}

---

## 8. Advanced 使用原则

**Advanced 技巧不默认使用。** 必须满足触发条件才可调用：

{"".join(f"{l}{chr(10)}" for l in adv_lines).rstrip() or "- 见 runtime_stage_rules.json 按阶段调用"}

---

## 9. Restricted 使用边界

**Restricted 技巧绝对不进入默认回复。** 以下是机制摘要，仅供理解风格原理：

{"".join(f"{l}{chr(10)}" for l in rest_lines).rstrip()}

---

## 10. 禁用模式

以下模式在任何场景下禁止：

{"".join(f"{l}{chr(10)}" for l in neg_lines).rstrip()}

---

## 11. 回复生成总原则

1. **当对方问问题时**，优先用反问或简短肯定，避免长篇解释
2. **当对方调侃时**，优先接梗或自嘲，避免防御或解释
3. **当对方示弱时**，优先轻调侃化解，避免过度关心或说教
4. **当对方追问时**，保持立场不变，避免因压力改变答案
5. **当对方生气时**，用幽默化解，避免道歉或辩解
6. **当对方夸奖时**，简单接受或轻调侃，避免谦虚或自夸
7. **当需要表达好感时**，用比喻或暗示，避免直白告白（除非触发 advanced）
8. **当需要设边界时**，用事实陈述，避免情绪化指责
9. **当对方沉默时**，给空间，避免追问或焦虑
10. **当话题无聊时**，用调侃转换，避免硬聊或冷场
11. **短句优先**，一个意思拆成多条发，制造节奏感
12. **不解释，不低姿态**，默认自己有价值

---

*完整规则见 v1_full/developer_style_rules.jsonl（91 条）*
*阶段规则见 v1_runtime/runtime_stage_rules.json*
"""

    # 如果超出 max_chars，截断末尾并加提示
    if len(prompt) > max_chars:
        prompt = prompt[:max_chars - 100]
        prompt += "\n\n*（内容已截断，完整版见 v1_full）*\n"

    return prompt


# ─── 主流程 ───────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="生成轻量运行时风格上下文 v1_runtime")
    parser.add_argument("--input-dir",       type=str, default="")
    parser.add_argument("--output-dir",      type=str, default="")
    parser.add_argument("--max-prompt-chars",type=int, default=6000)
    parser.add_argument("--dry-run",         action="store_true")
    args = parser.parse_args()

    in_dir  = Path(args.input_dir)  if args.input_dir  else IN_DIR
    out_dir = Path(args.output_dir) if args.output_dir else OUT_DIR

    # 读取输入
    profile      = load_json(in_dir / "style_profile.json")
    all_rules    = load_jsonl(in_dir / "developer_style_rules.jsonl")

    runtime_rules    = filter_runtime_rules(all_rules)
    negative_rules   = filter_negative_rules(all_rules)
    restricted_rules = filter_restricted_rules(all_rules)
    stage_rules      = filter_stage_rules(all_rules)
    stage_buckets    = bucket_stage_rules(stage_rules)

    prompt = build_runtime_prompt(
        profile, runtime_rules, negative_rules, restricted_rules,
        args.max_prompt_chars
    )

    print(f"\n{'='*60}")
    print(f"  build_runtime_style_context{'  [DRY-RUN]' if args.dry_run else ''}")
    print(f"  输入目录：{in_dir}")
    print(f"  规则总数：{len(all_rules)}")
    print(f"  runtime rules：{len(runtime_rules)}")
    print(f"  negative rules：{len(negative_rules)}")
    print(f"  restricted boundaries：{len(restricted_rules)}")
    print(f"  stage rules：{len(stage_rules)}")
    stage_filled = sum(1 for v in stage_buckets.values() if v)
    print(f"  stage 已覆盖阶段：{stage_filled}/8")
    print(f"  预计 prompt 字数：{len(prompt)}")
    print(f"{'='*60}\n")

    if args.dry_run:
        print("DRY-RUN 完成，未写入文件\n")
        return

    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. runtime_style_prompt.md
    prompt_path = out_dir / "runtime_style_prompt.md"
    prompt_path.write_text(prompt, encoding="utf-8")
    print(f"已写入：{prompt_path.name}  ({len(prompt)} 字符)")

    # 2. runtime_style_profile.json
    rt_profile = build_runtime_profile(profile)
    profile_path = out_dir / "runtime_style_profile.json"
    profile_path.write_text(
        json.dumps(rt_profile, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已写入：{profile_path.name}")

    # 3. runtime_reply_rules.json
    reply_rules_path = out_dir / "runtime_reply_rules.json"
    reply_rules_path.write_text(
        json.dumps([_slim_rule(r) for r in runtime_rules],
                   ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已写入：{reply_rules_path.name}  ({len(runtime_rules)} 条)")

    # 4. runtime_forbidden_rules.json
    forbidden = {
        "negative_rules":        [_slim_rule(r) for r in negative_rules],
        "restricted_boundaries": [_slim_rule(r) for r in restricted_rules],
    }
    forbidden_path = out_dir / "runtime_forbidden_rules.json"
    forbidden_path.write_text(
        json.dumps(forbidden, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已写入：{forbidden_path.name}  "
          f"(negative={len(negative_rules)}, restricted={len(restricted_rules)})")

    # 5. runtime_stage_rules.json
    stage_path = out_dir / "runtime_stage_rules.json"
    stage_path.write_text(
        json.dumps(stage_buckets, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已写入：{stage_path.name}  ({len(stage_rules)} 条，覆盖 {stage_filled}/8 阶段)")

    # 6. runtime_context_summary.json
    summary = {
        "created_at":            datetime.now().isoformat(),
        "source_dir":            str(in_dir),
        "output_dir":            str(out_dir),
        "rules_loaded":          len(all_rules),
        "runtime_rules":         len(runtime_rules),
        "negative_rules":        len(negative_rules),
        "restricted_boundaries": len(restricted_rules),
        "stage_rules":           len(stage_rules),
        "stage_coverage":        stage_filled,
        "prompt_chars":          len(prompt),
    }
    summary_path = out_dir / "runtime_context_summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已写入：{summary_path.name}\n")

    print(f"输出目录：{out_dir}\n")


if __name__ == "__main__":
    main()
