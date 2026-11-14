"""
scripts/build_mentor_cards.py
────────────────────────────
导师知识库 P2：把 chunks.json 里的 chunk 用 DeepSeek 结构化为知识卡片。
支持质量门控：card_type / relevance_score / noise_score / safety_level / include_in_tutor。
include_in_tutor=false 的卡片输出到 rejected 文件。

边界：
  - 默认只处理 10 个 chunk（命令行可调），不全量
  - 不接入任何核心 Agent，不接入现有知识库表
  - 不做向量检索 / Chroma / UI
  - 失败可重试 + 写入 report，可断点续跑

运行：
  python scripts/build_mentor_cards.py --sample 10
  python scripts/build_mentor_cards.py --mentor "<导师名>" --sample 15
  python scripts/build_mentor_cards.py --course "<课程名>" --sample 10
  python scripts/build_mentor_cards.py --dry-run
  python scripts/build_mentor_cards.py --resume
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from config import get_deepseek_config as _get_ds_cfg
_ds_cfg = _get_ds_cfg()
DEEPSEEK_API_KEY  = _ds_cfg.api_key
DEEPSEEK_MODEL    = _ds_cfg.model
DEEPSEEK_BASE_URL = _ds_cfg.base_url


# ─── DeepSeek Prompt ──────────────────────────────────────
SYSTEM_PROMPT = """你是一名导师课程内容结构化专家。任务：把一段导师讲座的逐字稿（chunk）提炼成一张知识卡片，并做质量和安全评估。

【硬规则】
1. 只输出严格 JSON，不要 markdown 代码块标记，不要任何额外解释或前后文字。
2. 所有字符串值必须在同一行，不能包含原始换行符。
3. 不要把原文长篇复述成总结；不要稀释成空话；不要写散文。
4. 保留原文里的实战表达、原话、案例细节。话术句子可以原样摘录到 example_lines。
5. 如果某字段没有内容，使用空字符串 "" 或空数组 []，不要编造。

【内容字段】
- concept：这段在讲什么，4-12 字短语，名词性。例："价值展示的边界"。
- core_claim：导师核心观点，一句话，20-50 字。要把"为什么这样做"讲清楚。
- applicable_scenarios：什么情况下用得上，每条 6-20 字，最多 4 条。
- common_mistakes：导师明确指出的误区/反例，每条 8-25 字，最多 4 条。
- actionable_moves：可执行动作或方法，每条 8-30 字，最多 4 条。每条要具体到能操作。
- example_lines：原文里的具体话术、对白、案例片段。直接摘录原话，每条最多 60 字，最多 4 条。如果原文没有话术就给空数组。
- risk_notes：风险、副作用、过度使用的代价、边界提醒。每条 8-30 字，最多 4 条。
- tags：3-6 个语义标签，名词或短词。

【质量门控字段】
- card_type：从以下选一个：
  · concept — 概念理论
  · tactic — 具体技巧
  · case — 案例复盘
  · dialogue_case — 真实对话/实战片段
  · mindset — 心态/认知原则
  · off_topic — 无关内容（金融业务闲聊、纯寒暄、课程开场废话、非两性/社交知识）
  · noisy — ASR 噪声、乱码、重复文本严重到无法提炼有效知识

- relevance_score：0-5 整数。
  5=高度适合导师问答，4=适合，3=有一定价值，1-2=价值弱，0=无关。

- noise_score：0-5 整数。
  0=干净，1-2=少量口癖/转写瑕疵，3=噪声明显但还能理解，4-5=乱码/重复/不可用。

- safety_level：从以下选一个：
  · low — 普通社交/聊天/约会/心态建议
  · medium — 涉及较强博弈、私密场合、性张力、强推进
  · high — 涉及犯罪案例引用、强操控、明显不适合直接执行的内容

- include_in_tutor：布尔值。是否应进入导师问答主库。
  规则：relevance_score >= 3 且 noise_score <= 2 且 card_type 不是 off_topic/noisy → true。
  否则 → false。

- skip_reason：如果 include_in_tutor=false，必须写原因（8-30 字）。
  例："ASR噪声严重，无法提炼有效知识"、"金融业务闲聊，非两性/社交知识"、"课程开场无实质内容"。
  如果 include_in_tutor=true，给空字符串 ""。

【安全约束】
- 如果内容涉及私密场合、性张力、强推进：
  · safety_level 必须设为 medium 或 high
  · risk_notes 必须包含边界提醒，如"必须确认对方舒适度""不施压""主动降级"
  · actionable_moves 不要生成鼓励施压、强推、操控的动作
  · example_lines 可以保留原文，但不要把危险行为包装成推荐话术
- 如果内容引用犯罪案例（如泰德邦迪等）：
  · safety_level=high
  · risk_notes 必须注明"仅作为反面案例引用，不可模仿"
  · actionable_moves 不要给出任何可执行的危险动作

【off_topic / noisy 判断】
- 如果 chunk 主要是金融业务讨论、产品定价、非两性/社交话题 → card_type=off_topic
- 如果 chunk 主要是乱码、重复文本、ASR 严重错误到无法理解 → card_type=noisy
- 不要把无关内容硬提炼成导师知识。off_topic/noisy 时其他内容字段可以简略或为空。

【风格约束】
- 直接、专业、像在做卡片摘要，不要导师腔。
- 不要混入 恋爱大师 风格 DNA，不要写"应该松弛""不要低姿态"这类元规则。
- 不要使用"作者""讲师"等称谓，直接说事实。"""


def build_user_prompt(chunk: dict) -> str:
    return f"""【来源】
mentor: {chunk.get('mentor', '')}
course: {chunk.get('course_name', '')}
file: {chunk.get('source_file', '')}
chunk_id: {chunk.get('chunk_id', '')}

【原文 chunk】
{chunk.get('text', '')}

请输出 JSON（所有字段必填）：
{{
  "concept": "",
  "core_claim": "",
  "applicable_scenarios": [],
  "common_mistakes": [],
  "actionable_moves": [],
  "example_lines": [],
  "risk_notes": [],
  "tags": [],
  "card_type": "",
  "relevance_score": 0,
  "noise_score": 0,
  "safety_level": "",
  "include_in_tutor": true,
  "skip_reason": ""
}}"""


# ─── 工具 ─────────────────────────────────────────────────
def _strip_codefence(raw: str) -> str:
    raw = raw.strip()
    if "```json" in raw:
        raw = raw.split("```json", 1)[1].split("```", 1)[0].strip()
    elif "```" in raw:
        raw = raw.split("```", 1)[1].split("```", 1)[0].strip()
    return raw


def _clean_json_newlines(s: str) -> str:
    out, in_str, esc = [], False, False
    for ch in s:
        if esc:
            out.append(ch); esc = False
        elif ch == "\\":
            out.append(ch); esc = True
        elif ch == '"':
            out.append(ch); in_str = not in_str
        elif ch in ("\n", "\r") and in_str:
            out.append(" ")
        else:
            out.append(ch)
    return "".join(out)


def _thinking_param(model: str) -> dict:
    if model.startswith("deepseek-v4-"):
        return {"thinking": {"type": "disabled"}}
    return {}


def call_deepseek_for_card(chunk: dict) -> tuple[dict | None, str | None]:
    try:
        from openai import OpenAI
        cfg = _get_ds_cfg()
        client = OpenAI(api_key=cfg.api_key, base_url=cfg.base_url,
                        timeout=cfg.timeout)
        resp = client.chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": build_user_prompt(chunk)},
            ],
            temperature=0.2,
            max_tokens=1500,
            extra_body=_thinking_param(DEEPSEEK_MODEL),
        )
        raw = resp.choices[0].message.content or ""
        raw = _strip_codefence(raw)
        raw = _clean_json_newlines(raw)
        data = json.loads(raw)
        if not isinstance(data, dict):
            return None, "non_dict_response"
        return data, None
    except json.JSONDecodeError as e:
        return None, f"json_decode_error: {e}"
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def build_card(chunk: dict, payload: dict) -> dict:
    text = chunk.get("text", "")
    excerpt = text[:300]
    return {
        "card_id": f"card_{chunk['chunk_id']}",
        "mentor": chunk.get("mentor", ""),
        "source_course": chunk.get("course_name", ""),
        "source_file": chunk.get("source_file", ""),
        "chunk_id": chunk["chunk_id"],
        "concept": payload.get("concept", "").strip(),
        "core_claim": payload.get("core_claim", "").strip(),
        "applicable_scenarios": payload.get("applicable_scenarios") or [],
        "common_mistakes": payload.get("common_mistakes") or [],
        "actionable_moves": payload.get("actionable_moves") or [],
        "example_lines": payload.get("example_lines") or [],
        "risk_notes": payload.get("risk_notes") or [],
        "tags": payload.get("tags") or [],
        "card_type": payload.get("card_type", "concept").strip(),
        "relevance_score": int(payload.get("relevance_score", 0)),
        "noise_score": int(payload.get("noise_score", 0)),
        "safety_level": payload.get("safety_level", "low").strip(),
        "include_in_tutor": bool(payload.get("include_in_tutor", True)),
        "skip_reason": payload.get("skip_reason", "").strip(),
        "quality_flags": [],
        "raw_excerpt": excerpt,
    }


# ─── 确定性后处理 ─────────────────────────────────────────
_VALID_CARD_TYPES = {"concept", "tactic", "case", "dialogue_case", "mindset", "off_topic", "noisy"}
_VALID_SAFETY_LEVELS = {"low", "medium", "high"}


def post_process_card(card: dict) -> tuple[dict, bool]:
    """
    确定性后处理：规范化字段 + 硬规则重算 include_in_tutor。
    返回 (card, was_adjusted)：was_adjusted=True 表示 include_in_tutor 被脚本层修正。
    """
    adjusted = False

    # 1. 规范化 card_type
    ct = card.get("card_type", "concept").strip().lower()
    if ct not in _VALID_CARD_TYPES:
        ct = "concept"
    card["card_type"] = ct

    # 2. clamp scores
    rel = max(0, min(5, int(card.get("relevance_score", 0))))
    noise = max(0, min(5, int(card.get("noise_score", 0))))
    card["relevance_score"] = rel
    card["noise_score"] = noise

    # 3. 规范化 safety_level
    sl = card.get("safety_level", "low").strip().lower()
    if sl not in _VALID_SAFETY_LEVELS:
        sl = "low"
    card["safety_level"] = sl

    # 4. 硬规则重算 include_in_tutor
    original_include = card.get("include_in_tutor", True)
    skip = card.get("skip_reason", "").strip()

    if ct in ("off_topic", "noisy"):
        include = False
        if not skip:
            skip = "无关内容或噪声文本"
    elif rel < 3:
        include = False
        if not skip:
            skip = "相关性不足"
    elif noise >= 4:
        include = False
        if not skip:
            skip = "ASR噪声过高"
    elif noise == 3 and rel < 4:
        include = False
        if not skip:
            skip = "噪声较高且相关性不足"
    else:
        include = True
        skip = ""

    if include != original_include:
        adjusted = True

    card["include_in_tutor"] = include
    card["skip_reason"] = skip

    # 5. quality_flags
    flags = []
    if noise == 3:
        flags.append("asr_noise_moderate")
    if sl == "high":
        flags.append("high_safety")
    if ct == "dialogue_case":
        flags.append("dialogue_case")
    elif ct == "case":
        flags.append("case_based")
    card["quality_flags"] = flags

    # 6. safety_level=high 但 risk_notes 为空 → 自动补一条
    if sl == "high" and not card.get("risk_notes"):
        card["risk_notes"] = ["内容涉及高风险场景，仅供分析参考，不可直接模仿或执行"]

    return card, adjusted


# ─── 样本筛选 ─────────────────────────────────────────────
def select_chunks(all_chunks: list, sample_n: int | None,
                  course: str | None, mentor: str | None) -> list:
    pool = all_chunks
    if course:
        pool = [c for c in pool if c.get("course_name") == course]
    if mentor:
        pool = [c for c in pool if c.get("mentor") == mentor]
    if sample_n is not None:
        return pool[:sample_n]
    return pool


def _default_rejected_path(out_path: Path) -> Path:
    stem = out_path.stem
    return out_path.parent / f"rejected_{stem}{out_path.suffix}"


# ─── 主流程 ───────────────────────────────────────────────
def main() -> int:
    parser = argparse.ArgumentParser(description="Mentor cards builder (P2)")
    parser.add_argument("--input", default=str(DEFAULT_INPUT))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--rejected-output", default=None,
                        help="rejected 卡片输出路径，默认自动推导")
    parser.add_argument("--report", default=str(DEFAULT_REPORT))
    parser.add_argument("--sample", type=int, default=DEFAULT_SAMPLE,
                        help="样本 chunk 数量，默认 10")
    parser.add_argument("--all", action="store_true", dest="process_all",
                        help="处理筛选后的全部 chunks（忽略 --sample）")
    parser.add_argument("--course", default=None, help="按 course_name 过滤")
    parser.add_argument("--mentor", default=None, help="按 mentor 过滤")
    parser.add_argument("--dry-run", action="store_true",
                        help="不调用 DeepSeek，只打印将处理的 chunk")
    parser.add_argument("--resume", action="store_true",
                        help="跳过已成功的 chunk_id")
    args = parser.parse_args()

    in_path = Path(args.input)
    out_path = Path(args.output)
    rejected_path = Path(args.rejected_output) if args.rejected_output else _default_rejected_path(out_path)
    report_path = Path(args.report)

    if not in_path.exists():
        print(f"[ERROR] chunks 文件不存在：{in_path}", file=sys.stderr)
        return 2

    all_chunks = json.loads(in_path.read_text(encoding="utf-8"))
    if not isinstance(all_chunks, list) or not all_chunks:
        print(f"[ERROR] chunks 文件为空或格式错误：{in_path}", file=sys.stderr)
        return 2

    sample_n = None if args.process_all else args.sample
    selected = select_chunks(all_chunks, sample_n, args.course, args.mentor)
    if not selected:
        print("[ERROR] 按筛选条件未找到任何 chunk", file=sys.stderr)
        return 2

    if args.process_all and not args.course and not args.mentor:
        print(f"[ALL] 将处理全部 {len(selected)} 个 chunk，预计耗时较长，"
              f"可用 Ctrl+C 中断后 --resume 继续", flush=True)

    # resume：读取已存在的 accepted + rejected
    existing_accepted: list = []
    existing_rejected: list = []
    existing_chunk_ids: set = set()
    if args.resume:
        if out_path.exists():
            try:
                existing_accepted = json.loads(out_path.read_text(encoding="utf-8"))
                if not isinstance(existing_accepted, list):
                    existing_accepted = []
            except Exception:
                existing_accepted = []
        if rejected_path.exists():
            try:
                existing_rejected = json.loads(rejected_path.read_text(encoding="utf-8"))
                if not isinstance(existing_rejected, list):
                    existing_rejected = []
            except Exception:
                existing_rejected = []
        existing_chunk_ids = {c.get("chunk_id") for c in existing_accepted + existing_rejected}

    if args.dry_run:
        print(f"[DRY-RUN] 将处理 {len(selected)} 个 chunk：")
        for c in selected:
            print(f"  · {c['chunk_id']}  {c.get('mentor','')}/{c.get('course_name','')}  ({c['char_count']} 字)")
        print(f"[DRY-RUN] output → {out_path}")
        print(f"[DRY-RUN] rejected → {rejected_path}")
        print("[DRY-RUN] 未调用 DeepSeek，未写入文件。")
        return 0

    if not DEEPSEEK_API_KEY:
        print("[ERROR] 未找到 DEEPSEEK_API_KEY（.env / 环境变量）。"
              "可使用 --dry-run 验证选样。", file=sys.stderr)
        return 2

    accepted: list = list(existing_accepted)
    rejected: list = list(existing_rejected)
    failures: list = []
    retries_log: list = []
    postprocess_notes: list = []
    postprocess_adjusted = 0
    started_at = time.time()

    for i, chunk in enumerate(selected, start=1):
        cid = chunk["chunk_id"]
        if cid in existing_chunk_ids:
            print(f"[{i}/{len(selected)}] skip (resume) {cid}")
            continue

        attempts = 0
        last_err = None
        payload = None
        print(f"[{i}/{len(selected)}] START {cid} · "
              f"{chunk.get('course_name','')}/{chunk.get('source_file','')} "
              f"({chunk.get('char_count',0)} 字)", flush=True)
        while attempts <= MAX_RETRIES:
            payload, err = call_deepseek_for_card(chunk)
            if payload is not None:
                break
            last_err = err
            attempts += 1
            if attempts <= MAX_RETRIES:
                wait = RETRY_BACKOFF_SEC * (2 ** (attempts - 1))
                retries_log.append({"chunk_id": cid, "attempt": attempts,
                                    "error": err, "wait_sec": wait})
                print(f"[{i}/{len(selected)}] retry {attempts}/{MAX_RETRIES} · "
                      f"{cid} · {err} · sleep {wait}s")
                time.sleep(wait)

        if payload is None:
            failures.append({"chunk_id": cid, "error": last_err,
                             "attempts": attempts})
            print(f"[{i}/{len(selected)}] FAIL {cid} · {last_err}")
            continue

        card = build_card(chunk, payload)
        card, was_adjusted = post_process_card(card)
        if was_adjusted:
            postprocess_adjusted += 1
            reason = (f"noise_score={card['noise_score']} "
                      f"relevance_score={card['relevance_score']} "
                      f"card_type={card['card_type']} → "
                      f"include_in_tutor forced to {card['include_in_tutor']}")
            postprocess_notes.append({"chunk_id": cid, "reason": reason})

        if card["include_in_tutor"]:
            accepted.append(card)
            label = "OK  "
        else:
            rejected.append(card)
            label = "REJ "
        adj_mark = " [adj]" if was_adjusted else ""
        print(f"[{i}/{len(selected)}] {label} {cid} · type={card['card_type']} "
              f"rel={card['relevance_score']} noise={card['noise_score']} "
              f"safety={card['safety_level']}{adj_mark} · '{card['concept'][:25]}'")

        # 实时落盘
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(accepted, ensure_ascii=False, indent=2),
                            encoding="utf-8")
        rejected_path.write_text(json.dumps(rejected, ensure_ascii=False, indent=2),
                                 encoding="utf-8")

    # 报告
    duration = round(time.time() - started_at, 1)
    new_accepted = len(accepted) - len(existing_accepted)
    new_rejected = len(rejected) - len(existing_rejected)

    # rejection 分类统计
    rejection_summary = {"off_topic": 0, "noisy": 0, "low_relevance": 0, "high_safety": 0}
    for c in rejected:
        ct = c.get("card_type", "")
        if ct == "off_topic":
            rejection_summary["off_topic"] += 1
        elif ct == "noisy":
            rejection_summary["noisy"] += 1
        elif c.get("relevance_score", 5) < 3:
            rejection_summary["low_relevance"] += 1
        if c.get("safety_level") == "high" and not c.get("include_in_tutor"):
            rejection_summary["high_safety"] += 1

    report = {
        "input": str(in_path).replace("\\", "/"),
        "output": str(out_path).replace("\\", "/"),
        "rejected_output": str(rejected_path).replace("\\", "/"),
        "processed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "duration_sec": duration,
        "model": DEEPSEEK_MODEL,
        "filter": {
            "all": args.process_all,
            "sample": args.sample if not args.process_all else None,
            "course": args.course,
            "mentor": args.mentor,
            "resume": args.resume,
        },
        "input_chunks_selected": len(selected),
        "cards_accepted_total": len(accepted),
        "cards_rejected_total": len(rejected),
        "cards_accepted_this_run": new_accepted,
        "cards_rejected_this_run": new_rejected,
        "postprocess_adjusted": postprocess_adjusted,
        "postprocess_notes": postprocess_notes,
        "failures": failures,
        "retries": retries_log,
        "rejection_summary": rejection_summary,
        "card_sources": [{"card_id": c["card_id"], "chunk_id": c["chunk_id"],
                          "mentor": c.get("mentor", ""),
                          "source_course": c.get("source_course", ""),
                          "include_in_tutor": c.get("include_in_tutor", True)}
                         for c in accepted + rejected],
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                           encoding="utf-8")

    print(f"\n[DONE] accepted {new_accepted} · rejected {new_rejected} · "
          f"failed {len(failures)} · 用时 {duration}s")
    print(f"[OK] accepted → {out_path}")
    print(f"[OK] rejected → {rejected_path}")
    print(f"[OK] report   → {report_path}")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
