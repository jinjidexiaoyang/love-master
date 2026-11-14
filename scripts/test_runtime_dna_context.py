"""
scripts/test_runtime_dna_context.py
本地验证 v1_runtime DNA 是否正确加载，不调用任何 API。

用法：
  python scripts/test_runtime_dna_context.py
  python scripts/test_runtime_dna_context.py --stage ambiguous
  python scripts/test_runtime_dna_context.py --refresh
"""

import sys
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from services.style_engine import get_runtime_style_context, get_style_engine


def main():
    parser = argparse.ArgumentParser(description="验证 v1_runtime DNA 加载状态")
    parser.add_argument("--stage",   type=str, default="",
                        help="测试指定关系阶段的 stage rules（如 ambiguous）")
    parser.add_argument("--refresh", action="store_true",
                        help="清除缓存重新读取")
    parser.add_argument("--inject",  action="store_true",
                        help="同时测试 engine.inject() 输出前300字")
    args = parser.parse_args()

    stage = args.stage or None

    print(f"\n{'='*60}")
    print(f"  v1_runtime DNA 加载验证")
    print(f"{'='*60}\n")

    ctx = get_runtime_style_context(stage=stage, refresh=args.refresh)

    # ── 加载状态 ──────────────────────────────────────────────
    if not ctx["loaded"]:
        print("❌ v1_runtime 未加载")
        print("   请先运行：python scripts/build_runtime_style_context.py")
        sys.exit(1)

    print(f"✅ 加载成功  source={ctx['source']}")
    print()

    # ── 数量统计 ──────────────────────────────────────────────
    prompt_chars   = len(ctx.get("prompt", ""))
    reply_rules    = ctx.get("reply_rules", [])
    forbidden      = ctx.get("forbidden", {})
    neg_rules      = forbidden.get("negative_rules", [])
    rest_bounds    = forbidden.get("restricted_boundaries", [])
    stage_rules    = ctx.get("stage_rules", [])
    profile        = ctx.get("profile", {})

    print(f"  prompt 字符数：       {prompt_chars}")
    print(f"  runtime reply rules： {len(reply_rules)} 条")
    print(f"  negative rules：      {len(neg_rules)} 条")
    print(f"  restricted bounds：   {len(rest_bounds)} 条")
    if stage:
        print(f"  stage rules ({stage})：{len(stage_rules)} 条")
    print()

    # ── profile 摘要 ──────────────────────────────────────────
    one_line = profile.get("one_line_definition", "（未找到）")
    traits   = profile.get("core_traits", [])
    print(f"  一句话定义：{one_line}")
    print(f"  core_traits：{len(traits)} 条")
    if traits:
        for t in traits[:3]:
            print(f"    · {t}")
        if len(traits) > 3:
            print(f"    ... 共 {len(traits)} 条")
    print()

    # ── runtime rules 分类统计 ────────────────────────────────
    from collections import Counter
    type_counts = Counter(r.get("rule_type", "?") for r in reply_rules)
    print(f"  runtime rules 分类：")
    for rt, cnt in sorted(type_counts.items()):
        print(f"    {rt:<20} {cnt} 条")
    print()

    # ── stage rules 预览 ─────────────────────────────────────
    if stage and stage_rules:
        print(f"  stage rules ({stage}) 预览：")
        for r in stage_rules[:3]:
            print(f"    [{r.get('rule_id','')}] {r.get('title','')}")
            print(f"      {r.get('content','')[:80]}")
        print()

    # ── prompt 前500字 ────────────────────────────────────────
    prompt_text = ctx.get("prompt", "")
    print(f"  runtime_style_prompt.md 前500字：")
    print(f"{'─'*60}")
    print(prompt_text[:500])
    print(f"{'─'*60}")
    print()

    # ── inject() 测试 ─────────────────────────────────────────
    if args.inject:
        engine = get_style_engine()
        injected = engine.inject(
            base_prompt="[TEST BASE PROMPT]",
            include_cases=False,
            stage=stage,
        )
        print(f"  engine.inject() 输出前300字：")
        print(f"{'─'*60}")
        print(injected[:300])
        print(f"{'─'*60}")
        print()

    print("验证完成\n")


if __name__ == "__main__":
    main()
