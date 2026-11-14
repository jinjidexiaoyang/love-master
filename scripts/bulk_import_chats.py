"""
scripts/bulk_import_chats.py — 批量导入聊天记录 txt 文件

目录约定：
  data/import_inbox/    ← 把 txt 文件放这里
  data/import_done/     ← 导入成功后自动移动到这里
  data/import_failed/   ← 解析失败或无消息的文件移动到这里
  data/import_logs/     ← 每次运行生成一份日志

文件命名规则（支持以下格式）：
  私聊_mayira.txt            → mayira
  私聊_alnur 阿克苏.txt      → alnur（取空格前第一个词）
  chat_mayira_01.txt         → mayira
  mayira.txt                 → mayira

推荐操作路径：
  第一步：dry-run 预览，确认识别正确
    python scripts/bulk_import_chats.py --dry-run

  第二步：正式导入，暂不分析（稳妥）
    python scripts/bulk_import_chats.py --no-analyze

  第三步：确认数据质量后，再去网页手动触发分析
    或：python scripts/bulk_import_chats.py --contact mayira（单文件重跑+分析）
"""

import sys
import os
import shutil
import argparse
from datetime import datetime
from pathlib import Path

# 把项目根目录加入 sys.path，确保能 import 项目模块
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from services.ingestion_service import auto_detect_and_parse, import_chat_to_db, detect_contact_name_from_text
from database import init_db, add_contact, get_contact

# ─── 目录配置 ─────────────────────────────────────────────────
INBOX_DIR   = ROOT / "data" / "import_inbox"
DONE_DIR    = ROOT / "data" / "import_done"
FAILED_DIR  = ROOT / "data" / "import_failed"
LOG_DIR     = ROOT / "data" / "import_logs"

for d in [INBOX_DIR, DONE_DIR, FAILED_DIR, LOG_DIR]:
    d.mkdir(parents=True, exist_ok=True)


# ─── 从文件名提取联系人名 ──────────────────────────────────────
def extract_name_from_filename(filename: str) -> str:
    """
    支持以下命名格式：
      私聊_mayira.txt          → mayira
      私聊_alnur 阿克苏.txt    → alnur（取空格前第一个词）
      私聊_1ann.txt            → 1ann
      chat_mayira_01.txt       → mayira
      mayira.txt               → mayira

    规则：
      1. 去掉 .txt 后缀
      2. 去掉 私聊_ / chat_ 前缀
      3. 如果剩余部分含空格，取第一个词（空格前）
      4. 如果剩余部分含下划线且不像日期，取第一段
    """
    stem = Path(filename).stem  # 去掉 .txt

    # 去掉已知前缀
    for prefix in ("私聊_", "chat_", "群聊_"):
        if stem.startswith(prefix):
            stem = stem[len(prefix):]
            break

    # 含空格：取第一个词（如 "alnur 阿克苏" → "alnur"）
    if " " in stem:
        return stem.split(" ")[0].strip()

    # 含下划线：如果第一段不是纯数字/日期，取第一段
    if "_" in stem:
        parts = stem.split("_")
        first = parts[0].strip()
        if first and not first.isdigit():
            return first

    return stem.strip()


# ─── 单文件处理 ───────────────────────────────────────────────
def process_file(
    filepath: Path,
    contact_override: str = "",
    dry_run: bool = False,
    do_analyze: bool = True,
) -> dict:
    """
    处理单个 txt 文件，返回结果字典：
    {
        "file": str,
        "contact": str,
        "format": str,
        "count": int,
        "success": bool,
        "error": str,
        "analyzed": bool,
    }
    """
    result = {
        "file": filepath.name,
        "contact": "",
        "format": "",
        "count": 0,
        "success": False,
        "error": "",
        "analyzed": False,
    }

    # 读取文件
    try:
        text = filepath.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            text = filepath.read_text(encoding="gbk")
        except Exception as e:
            result["error"] = f"文件编码无法识别：{e}"
            return result

    if not text.strip():
        result["error"] = "文件内容为空"
        return result

    # 确定联系人名
    contact_name = contact_override.strip()
    if not contact_name:
        contact_name = extract_name_from_filename(filepath.name)
    if not contact_name:
        contact_name = detect_contact_name_from_text(text)
    if not contact_name:
        result["error"] = "无法识别联系人名（文件名不含联系人，内容也无法自动识别）。请用 --contact 参数指定，或按 chat_<名字>_xx.txt 命名文件。"
        return result

    result["contact"] = contact_name

    # 解析消息
    fmt, messages = auto_detect_and_parse(text, contact_name)
    result["format"] = fmt
    result["count"] = len(messages)

    if not messages:
        result["error"] = f"解析为 {fmt} 格式，但未提取到任何消息"
        return result

    if dry_run:
        result["success"] = True
        return result

    # 写入数据库
    if not get_contact(contact_name):
        add_contact(contact_name)

    imported = import_chat_to_db(contact_name, messages)
    result["count"] = imported

    # 可选：AI 分析
    if do_analyze and imported > 0:
        try:
            from agents.analyzer import analyze_conversation
            from utils.scoring import score_from_analysis
            analyze_conversation(contact_name, messages)
            result["analyzed"] = True
        except Exception as e:
            result["analyzed"] = False
            result["error"] = f"导入成功，但 AI 分析失败：{e}"

    result["success"] = True
    return result


# ─── 主流程 ───────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="批量导入聊天记录 txt 文件")
    parser.add_argument("--dry-run", action="store_true", help="仅预览，不写入数据库，不移动文件")
    parser.add_argument("--no-analyze", action="store_true", help="只导入消息，不触发 AI 分析（推荐首次使用）")
    parser.add_argument("--analyze", action="store_true", help="导入同时触发 AI 分析（需消耗 API）")
    parser.add_argument("--contact", type=str, default="", help="手动指定联系人名（覆盖自动识别）")
    args = parser.parse_args()

    # 默认不分析；只有明确传 --analyze 才开启
    do_analyze = args.analyze and not args.no_analyze
    dry_run = args.dry_run

    # 初始化数据库
    if not dry_run:
        init_db()

    # 扫描 inbox
    txt_files = sorted(INBOX_DIR.glob("*.txt"))
    if not txt_files:
        print(f"\n[提示] {INBOX_DIR} 目录下没有找到 .txt 文件，请先把聊天记录放进去。\n")
        return

    mode_label = "[DRY-RUN 预览模式]" if dry_run else ("[仅导入，不分析]" if not do_analyze else "[导入 + AI 分析]")
    print(f"\n{'='*60}")
    print(f"  恋爱大师 批量导入  {mode_label}")
    print(f"  共发现 {len(txt_files)} 个文件")
    print(f"{'='*60}\n")

    # 日志
    log_lines = [f"批量导入日志 — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", f"模式：{mode_label}", ""]
    results = []

    for i, fp in enumerate(txt_files, 1):
        print(f"[{i}/{len(txt_files)}] 处理：{fp.name}")
        res = process_file(fp, contact_override=args.contact, dry_run=dry_run, do_analyze=do_analyze)
        results.append(res)

        if res["success"]:
            analyze_tag = "（已分析）" if res["analyzed"] else ("（未分析）" if do_analyze else "")
            print(f"  ✓ 联系人：{res['contact']}  格式：{res['format']}  消息：{res['count']} 条{analyze_tag}")
            if res.get("error"):  # 分析失败但导入成功
                print(f"  ⚠ {res['error']}")
            log_lines.append(f"✓ {fp.name} → {res['contact']} | {res['format']} | {res['count']} 条{analyze_tag}")
            # 移动文件
            if not dry_run:
                dest = DONE_DIR / fp.name
                if dest.exists():
                    dest = DONE_DIR / f"{fp.stem}_{datetime.now().strftime('%H%M%S')}.txt"
                shutil.move(str(fp), str(dest))
        else:
            print(f"  ✗ 失败：{res['error']}")
            log_lines.append(f"✗ {fp.name} | 错误：{res['error']}")
            if not dry_run:
                dest = FAILED_DIR / fp.name
                if dest.exists():
                    dest = FAILED_DIR / f"{fp.stem}_{datetime.now().strftime('%H%M%S')}.txt"
                shutil.move(str(fp), str(dest))

    # 汇总
    ok = sum(1 for r in results if r["success"])
    fail = len(results) - ok
    print(f"\n{'─'*60}")
    print(f"  完成：{ok} 成功 / {fail} 失败 / 共 {len(results)} 个文件")
    if dry_run:
        print(f"\n  这是预览模式，数据库未写入。")
        print(f"  确认识别正确后，运行：")
        print(f"    python scripts/bulk_import_chats.py --no-analyze")
    else:
        print(f"  成功文件 → {DONE_DIR}")
        if fail:
            print(f"  失败文件 → {FAILED_DIR}（查看上方错误提示）")
        if not do_analyze:
            print(f"\n  消息已导入，AI 分析未触发。")
            print(f"  去网页「聊天分析」页手动分析，或运行：")
            print(f"    python scripts/bulk_import_chats.py --analyze（对 inbox 中新文件）")
    print(f"{'─'*60}\n")

    # 写日志
    if not dry_run:
        log_lines += ["", f"汇总：{ok} 成功 / {fail} 失败"]
        log_path = LOG_DIR / f"import_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        log_path.write_text("\n".join(log_lines), encoding="utf-8")
        print(f"  日志已保存：{log_path}\n")


if __name__ == "__main__":
    main()
