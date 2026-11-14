"""
scripts/process_mentor_videos.py
───────────────────────────────
导师视频文本 P1：清洗 + 切块 + 生成 chunks.json

 输入：processed_dir 指向的目录下所有 .txt（默认 data/mentor_videos）
输出：
  data/mentor_knowledge/chunks.json
  data/mentor_knowledge/process_report.json

硬边界：
  - 不调用任何 LLM（DeepSeek / Claude / OpenAI 等）
  - 不生成卡片，不做向量化
  - 不读写 videos 目录之外的源文件
  - 不改数据库、不改现有 pages / agents / style_engine / DNA

运行：python scripts/process_mentor_videos.py
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Iterable

# ─── 配置 ─────────────────────────────────────────────────
INPUT_DIR = Path("data/mentor_videos")
OUTPUT_DIR = Path("data/mentor_knowledge")
CHUNKS_PATH = OUTPUT_DIR / "chunks.json"
REPORT_PATH = OUTPUT_DIR / "process_report.json"

MIN_CLEAN_CHARS = 100       # 清洗后少于 100 字视为太短，跳过
TARGET_CHUNK_CHARS = 700    # 目标 chunk 大小
SOFT_MAX_CHUNK_CHARS = 900  # 软上限，超过就尽量 flush
MAX_CHUNK_CHARS = 1200      # 硬上限，绝对不超过
MIN_CHUNK_CHARS = 200       # 低于此视为过短，尽量合并
TINY_TAIL_DROP_THRESHOLD = 100  # 尾段 < 此阈值且无法合并则丢弃

# ─── 清洗正则 ─────────────────────────────────────────────
# 统一换行
_RE_CRLF = re.compile(r"\r\n?")
# 连续空行压成单空行
_RE_MULTI_BLANKLINE = re.compile(r"\n[ \t]*\n[\s\n]*")
# 连续空格压成单空格（仅在行内）
_RE_INLINE_WS = re.compile(r"[ \t]{2,}")

# 口癖剥离：放在词边界较弱的中文场景里，只处理几种保守模式
# 只删：独立成词、或夹在标点/空格之间的极短填充语
# 不删："那个人" 中的 "那个"、"就是说" 作为承接的完整结构时也尽量保守
# 策略：只处理「口癖 + 逗号/句号/空白」和「空白/句首 + 口癖 + 空白」这种明显的填充
_FILLERS = ["呃", "嗯", "啊", "哦", "诶", "嘿"]
# 形如："，呃，" / "。嗯。" / " 啊 " / 行首/行尾的填充
_RE_FILLER_PUNCT = re.compile(
    r"([，。！？、；：\s])(" + "|".join(_FILLERS) + r")+([，。！？、；：\s])"
)
_RE_FILLER_LEAD = re.compile(r"^((?:" + "|".join(_FILLERS) + r")[，。、\s]+)+")
_RE_FILLER_TAIL = re.compile(r"(?:(?:" + "|".join(_FILLERS) + r")[，。、\s]*)+$")

# 重复短语：连续出现 2+ 次的 1-3 字短片段，合并为一次
# 例："然后然后然后" → "然后"；"那个那个" → "那个"
_RE_REPEAT_SHORT = re.compile(r"(.{1,3}?)\1{2,}")

# 断句用标点（按优先级）
_SENT_ENDINGS = set("。！？!?…")
_SOFT_PUNCT = set("，、；,;")  # 软标点：次优切分点
_SPACES = set(" \t")


# ─── 清洗 ─────────────────────────────────────────────────
def clean_text(raw: str) -> str:
    """轻量清洗：统一换行、去多余空白、去常见口癖、合并短重复、合并过短行。"""
    if not raw:
        return ""
    text = _RE_CRLF.sub("\n", raw)

    # 去 BOM
    if text.startswith("﻿"):
        text = text[1:]

    # 行级清洗
    lines = []
    for line in text.split("\n"):
        s = line.strip()
        if not s:
            lines.append("")
            continue
        s = _RE_INLINE_WS.sub(" ", s)
        # 行首/行尾填充
        s = _RE_FILLER_LEAD.sub("", s)
        s = _RE_FILLER_TAIL.sub("", s)
        # 行内填充：迭代几次以吃掉相邻
        for _ in range(3):
            new_s = _RE_FILLER_PUNCT.sub(lambda m: m.group(1) + m.group(3), s)
            if new_s == s:
                break
            s = new_s
        # 合并短重复："然后然后" 之类
        s = _RE_REPEAT_SHORT.sub(r"\1", s)
        lines.append(s)

    text = "\n".join(lines)
    # 连续空行压成单空行
    text = _RE_MULTI_BLANKLINE.sub("\n\n", text)
    # 合并过短行（< 8 字且下一行非空）→ 与下一行拼接
    merged: list[str] = []
    buf = ""
    for line in text.split("\n"):
        if not line.strip():
            if buf:
                merged.append(buf)
                buf = ""
            merged.append("")
            continue
        if buf:
            # 前一行极短 → 合并
            if len(buf) < 8:
                buf = buf + line.strip()
            else:
                merged.append(buf)
                buf = line.strip()
        else:
            buf = line.strip()
    if buf:
        merged.append(buf)
    text = "\n".join(merged)

    # 再次压一次空行
    text = _RE_MULTI_BLANKLINE.sub("\n\n", text)
    return text.strip()


# ─── 切块 ─────────────────────────────────────────────────
def _split_long_segment(text: str) -> list[str]:
    """
    把一个超过 MAX_CHUNK_CHARS 的段切成若干 <= MAX 的片段。
    切点偏好：优先在 [TARGET, SOFT_MAX] 里找句末标点；
              再在 [TARGET, MAX] 里找软标点；
              再扩大到 [MIN, MAX] 找任何可用切点；
              最后才硬切在 SOFT_MAX。
    """
    parts: list[str] = []
    remaining = text
    while len(remaining) > MAX_CHUNK_CHARS:
        n = len(remaining)
        # 窗口层次从严到松
        windows = [
            (TARGET_CHUNK_CHARS, SOFT_MAX_CHUNK_CHARS, _SENT_ENDINGS),
            (TARGET_CHUNK_CHARS, MAX_CHUNK_CHARS, _SENT_ENDINGS),
            (TARGET_CHUNK_CHARS, SOFT_MAX_CHUNK_CHARS, _SOFT_PUNCT),
            (TARGET_CHUNK_CHARS, MAX_CHUNK_CHARS, _SOFT_PUNCT),
            (MIN_CHUNK_CHARS, MAX_CHUNK_CHARS, _SOFT_PUNCT),
            (MIN_CHUNK_CHARS, MAX_CHUNK_CHARS, _SPACES),
        ]
        cut = -1
        for lo, hi, targets in windows:
            hi_eff = min(hi, n)
            lo_eff = min(lo, hi_eff)
            for i in range(hi_eff - 1, lo_eff - 1, -1):
                if remaining[i] in targets:
                    cut = i
                    break
            if cut >= 0:
                break
        if cut < 0:
            # 真的找不到合适切点 → 在 SOFT_MAX 处硬切
            cut = min(SOFT_MAX_CHUNK_CHARS, n) - 1

        piece = remaining[: cut + 1].strip()
        if piece:
            parts.append(piece)
        remaining = remaining[cut + 1:].lstrip()

    if remaining.strip():
        parts.append(remaining.strip())
    return [p for p in parts if p]


def _iter_segments(cleaned: str) -> list[str]:
    """
    把清洗后的文本拆成 segment 列表：
      先按空行切大段 → 每个大段再按单换行切 segment → strip → 过滤空。
    """
    segments: list[str] = []
    for big in re.split(r"\n\s*\n", cleaned):
        big = big.strip()
        if not big:
            continue
        for line in big.split("\n"):
            s = line.strip()
            if s:
                segments.append(s)
        # 大段之间加一个空标记，便于 chunk_text 识别"硬边界"
        segments.append("")  # sentinel
    # 去掉末尾多余 sentinel
    while segments and segments[-1] == "":
        segments.pop()
    return segments


def chunk_text(text: str) -> list[str]:
    """
    基于 segment 的聚合式切块：
      - 累积 segment 到 buf，目标 TARGET_CHUNK_CHARS
      - 加入下一 segment 会超过 SOFT_MAX_CHUNK_CHARS → flush
      - 单 segment 超过 MAX_CHUNK_CHARS → 走 _split_long_segment
      - 空行 sentinel 遇到时：若 buf >= MIN 则 flush（尊重作者的段落意图）
    """
    if not text:
        return []

    segments = _iter_segments(text)
    if not segments:
        return []

    chunks: list[str] = []
    buf = ""

    def flush():
        nonlocal buf
        if buf.strip():
            chunks.append(buf.strip())
        buf = ""

    for seg in segments:
        # 硬边界（空行 sentinel）：buf 已达 MIN 就 flush
        if seg == "":
            if len(buf) >= MIN_CHUNK_CHARS:
                flush()
            continue

        # 超长 segment 单独处理
        if len(seg) > MAX_CHUNK_CHARS:
            # 先把 buf flush（如果有且非过短）
            if buf:
                if len(buf) >= MIN_CHUNK_CHARS:
                    flush()
                else:
                    # buf 太短，尝试让它跟超长段的首片段合并
                    long_parts = _split_long_segment(seg)
                    if long_parts:
                        head = long_parts[0]
                        if len(buf) + 2 + len(head) <= MAX_CHUNK_CHARS:
                            buf = buf + "\n" + head
                            flush()
                            for p in long_parts[1:]:
                                chunks.append(p)
                        else:
                            flush()
                            for p in long_parts:
                                chunks.append(p)
                    else:
                        flush()
                    continue
            for p in _split_long_segment(seg):
                chunks.append(p)
            continue

        # 常规 segment
        if not buf:
            buf = seg
            continue

        prospective = len(buf) + 1 + len(seg)  # +1 是换行分隔
        if prospective <= TARGET_CHUNK_CHARS:
            # 远未到目标，放心合并
            buf = buf + "\n" + seg
        elif prospective <= SOFT_MAX_CHUNK_CHARS:
            # 在 TARGET ~ SOFT_MAX 区间：若 buf 已到 TARGET 就 flush，否则继续合并
            if len(buf) >= TARGET_CHUNK_CHARS:
                flush()
                buf = seg
            else:
                buf = buf + "\n" + seg
        else:
            # 合并会超过 SOFT_MAX → 优先 flush
            if len(buf) >= MIN_CHUNK_CHARS:
                flush()
                buf = seg
            else:
                # buf 过短：只能合并（即使会超 SOFT_MAX），但别超 MAX
                if prospective <= MAX_CHUNK_CHARS:
                    buf = buf + "\n" + seg
                else:
                    # 都不行：把 seg 当超长段切
                    if buf:
                        flush()
                    for p in _split_long_segment(seg):
                        chunks.append(p)

    flush()

    # 尾段处理：若最后一个 chunk < MIN，尝试回合到前一个
    if len(chunks) >= 2 and len(chunks[-1]) < MIN_CHUNK_CHARS:
        last = chunks[-1]
        prev = chunks[-2]
        if len(prev) + 1 + len(last) <= MAX_CHUNK_CHARS:
            chunks[-2] = prev + "\n" + last
            chunks.pop()
        # 无法合并 → 保留或外层丢弃交由 process_one_file 判断
    return chunks


# ─── 元信息推断 ───────────────────────────────────────────
# 默认规则表为空：使用者在此添加自己导师的 (文件名关键词, 导师名) 规则，
# 用于从转写文件名/路径推断导师归属。
_MENTOR_RULES = [
    # ("你的导师关键词", "导师名"),
]


def infer_mentor(course_dir: str, file_name: str) -> str:
    blob = f"{course_dir} {file_name}"
    for keyword, label in _MENTOR_RULES:
        if keyword in blob:
            return label
    return "unknown"


def safe_slug(s: str, max_len: int = 40) -> str:
    """把路径片段变为 id 片段：去非法字符，保留字母数字下划线短横。"""
    cleaned = re.sub(r"[^A-Za-z0-9一-鿿_\-]", "_", s)
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    return cleaned[:max_len] or "x"


def make_chunk_id(rel_path: str, idx: int) -> str:
    """稳定唯一 ID：相对路径哈希 + 索引。同文件重跑 ID 不变。"""
    h = hashlib.md5(rel_path.encode("utf-8")).hexdigest()[:10]
    return f"mentor_{h}_chunk_{idx:04d}"


# ─── 主流程 ───────────────────────────────────────────────
def iter_txt_files(root: Path) -> Iterable[Path]:
    for p in sorted(root.rglob("*.txt")):
        if p.is_file():
            yield p


def read_text_best_effort(path: Path) -> str | None:
    for enc in ("utf-8", "utf-8-sig", "gb18030", "gbk"):
        try:
            return path.read_text(encoding=enc)
        except UnicodeDecodeError:
            continue
        except Exception:
            return None
    return None


def process_one_file(path: Path, root: Path) -> tuple[list[dict], dict | None, dict | None, int]:
    """处理单个文件。返回 (chunks, skipped_entry_or_None, failed_entry_or_None, tiny_tail_dropped)。"""
    rel = path.relative_to(root)
    rel_posix = rel.as_posix()
    course_dir = rel.parts[0] if len(rel.parts) > 1 else ""
    file_name = path.name

    raw = read_text_best_effort(path)
    if raw is None:
        return [], None, {"path": str(path), "error": "decode_failed"}, 0

    cleaned = clean_text(raw)
    if len(cleaned) < MIN_CLEAN_CHARS:
        return [], {"path": str(path), "reason": "empty_or_too_short",
                    "clean_chars": len(cleaned)}, None, 0

    pieces = chunk_text(cleaned)
    if not pieces:
        return [], {"path": str(path), "reason": "no_chunks_after_split",
                    "clean_chars": len(cleaned)}, None, 0

    # 兜底：丢弃过短尾段（仅当多 chunk 且无法回合到前一段时才会到这一步）
    tiny_dropped = 0
    if len(pieces) >= 2 and len(pieces[-1]) < TINY_TAIL_DROP_THRESHOLD:
        pieces = pieces[:-1]
        tiny_dropped = 1

    mentor = infer_mentor(course_dir, file_name)
    course_name = course_dir or "unknown_course"

    chunks: list[dict] = []
    for i, text in enumerate(pieces, start=1):
        chunks.append({
            "chunk_id": make_chunk_id(rel_posix, i),
            "source_type": "mentor_video",
            "source_dir": course_dir,
            "source_root": str(root),
            "source_file": file_name,
            "relative_path": rel_posix,
            "mentor": mentor,
            "course_name": course_name,
            "chunk_index": i,
            "char_count": len(text),
            "text": text,
        })
    return chunks, None, None, tiny_dropped


def main() -> int:
    if not INPUT_DIR.exists():
        print(f"[ERROR] 输入目录不存在：{INPUT_DIR}", file=sys.stderr)
        return 2

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    all_chunks: list[dict] = []
    skipped: list[dict] = []
    failed: list[dict] = []
    chunks_by_course: dict[str, int] = {}
    tiny_tail_dropped = 0

    all_txt = list(iter_txt_files(INPUT_DIR))
    # 目录数（只统计直接含 txt 的一级子目录）
    top_dirs = {p.relative_to(INPUT_DIR).parts[0]
                for p in all_txt if len(p.relative_to(INPUT_DIR).parts) >= 1}

    processed_files = 0
    for path in all_txt:
        try:
            chunks, skip_entry, fail_entry, tiny = process_one_file(path, INPUT_DIR)
        except Exception as e:
            failed.append({"path": str(path), "error": f"{type(e).__name__}: {e}"})
            continue

        if fail_entry:
            failed.append(fail_entry)
            continue
        if skip_entry:
            skipped.append(skip_entry)
            continue

        tiny_tail_dropped += tiny
        all_chunks.extend(chunks)
        processed_files += 1
        course = chunks[0]["course_name"] if chunks else ""
        if course:
            chunks_by_course[course] = chunks_by_course.get(course, 0) + len(chunks)

    # 统计
    char_counts = sorted(c["char_count"] for c in all_chunks)
    total_chunks = len(all_chunks)

    def _percentile(sorted_vals: list[int], p: float) -> int:
        if not sorted_vals:
            return 0
        k = max(0, min(len(sorted_vals) - 1, int(round(p * (len(sorted_vals) - 1)))))
        return sorted_vals[k]

    median_chunk_chars = _percentile(char_counts, 0.5)
    p90_chunk_chars = _percentile(char_counts, 0.9)
    small_chunks_count = sum(1 for v in char_counts if v < MIN_CHUNK_CHARS)
    max_sized_chunks_count = sum(1 for v in char_counts if v >= MAX_CHUNK_CHARS - 10)

    report = {
        "input_dir": str(INPUT_DIR),
        "output_chunks": str(CHUNKS_PATH).replace("\\", "/"),
        "processed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total_dirs": len(top_dirs),
        "total_txt_files": len(all_txt),
        "processed_files": processed_files,
        "skipped_files": skipped,
        "failed_files": failed,
        "total_chunks": total_chunks,
        "min_chunk_chars": char_counts[0] if char_counts else 0,
        "max_chunk_chars": char_counts[-1] if char_counts else 0,
        "avg_chunk_chars": (sum(char_counts) // total_chunks) if total_chunks else 0,
        "median_chunk_chars": median_chunk_chars,
        "p90_chunk_chars": p90_chunk_chars,
        "small_chunks_count": small_chunks_count,
        "max_sized_chunks_count": max_sized_chunks_count,
        "tiny_tail_dropped": tiny_tail_dropped,
        "chunks_by_course": chunks_by_course,
        "config": {
            "MIN_CLEAN_CHARS": MIN_CLEAN_CHARS,
            "TARGET_CHUNK_CHARS": TARGET_CHUNK_CHARS,
            "SOFT_MAX_CHUNK_CHARS": SOFT_MAX_CHUNK_CHARS,
            "MAX_CHUNK_CHARS": MAX_CHUNK_CHARS,
            "MIN_CHUNK_CHARS": MIN_CHUNK_CHARS,
            "TINY_TAIL_DROP_THRESHOLD": TINY_TAIL_DROP_THRESHOLD,
        },
    }

    CHUNKS_PATH.write_text(
        json.dumps(all_chunks, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    REPORT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"[OK] 处理文件 {processed_files}/{len(all_txt)} · "
          f"生成 chunk {total_chunks}")
    print(f"[OK] chunks → {CHUNKS_PATH}")
    print(f"[OK] report → {REPORT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
