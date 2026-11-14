"""
mentor_card_review_service — P4C 卡片逐条审核闸门(P1)

职责:
  - 在 batch staging 内维护逐条审核状态 review_state.json
  - 原始卡始终以 draft_cards.json 为准;review_state 只存
    decision / edited / updated_at / reviewer / review_notes / card(编辑后版本)
  - “重置本卡”=删除该卡的 review 条目,UI 重新从 draft_cards.json 读取
  - 导出 approved_cards.json(bare list,供 approve 脚本合并)
    + rejected_cards_reviewed.json(审计用)
  - 不调用 LLM,不碰正式 cards/chunks/profiles

落盘文件(均在 batch staging 目录下):
  draft_cards.json              # 卡片化产物(只读,权威原始卡)
  review_state.json             # 逐条审核状态(本服务维护)
  approved_cards.json           # 导出:仅 approved 的最终卡(bare list)
  rejected_cards_reviewed.json  # 导出:人工 reject 的卡 + 审核备注
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone

from services.mentor_ingest_service import batch_dir

SCHEMA_VERSION = 1
DECISIONS = ("pending", "approved", "rejected", "needs_regenerate")
# P1 第一版最小可编辑字段(applicable_scenarios / example_lines 等先只读)
EDITABLE_FIELDS = (
    "concept", "core_claim", "actionable_moves",
    "common_mistakes", "risk_notes", "tags", "include_in_tutor",
)
LIST_FIELDS = ("actionable_moves", "common_mistakes", "risk_notes", "tags")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ─── 路径 ─────────────────────────────────────────────────
def draft_cards_path(batch_id: str):
    return batch_dir(batch_id) / "draft_cards.json"


def review_state_path(batch_id: str):
    return batch_dir(batch_id) / "review_state.json"


def approved_cards_path(batch_id: str):
    return batch_dir(batch_id) / "approved_cards.json"


def rejected_reviewed_path(batch_id: str):
    return batch_dir(batch_id) / "rejected_cards_reviewed.json"


# ─── JSON I/O(原子写)─────────────────────────────────────
def _read_json(path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _atomic_write_json(path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}.{uuid.uuid4().hex[:6]}")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


# ─── draft / state 读取 ───────────────────────────────────
def load_draft_cards(batch_id: str) -> list[dict]:
    v = _read_json(draft_cards_path(batch_id), [])
    return v if isinstance(v, list) else []


def _empty_state(batch_id: str) -> dict:
    return {
        "batch_id": batch_id,
        "schema_version": SCHEMA_VERSION,
        "updated_at": _now_iso(),
        "reviewer": "admin",
        "cards": {},
    }


def load_state(batch_id: str) -> dict:
    st = _read_json(review_state_path(batch_id), None)
    if not isinstance(st, dict) or "cards" not in st:
        return _empty_state(batch_id)
    st.setdefault("cards", {})
    return st


def save_state(batch_id: str, state: dict) -> None:
    state["updated_at"] = _now_iso()
    _atomic_write_json(review_state_path(batch_id), state)


# ─── 合并视图:draft + review 叠加 ────────────────────────
def _decision_of(state: dict, card_id: str) -> str:
    return (state.get("cards", {}).get(card_id) or {}).get("decision", "pending")


def effective_card(draft_card: dict, entry: dict | None) -> dict:
    """返回当前生效卡:有编辑过的 card 用编辑版,否则用 draft 原卡。"""
    if entry and isinstance(entry.get("card"), dict):
        return entry["card"]
    return draft_card


def list_review_rows(batch_id: str) -> list[dict]:
    """逐条返回 {card_id, draft, entry, decision, effective}。draft 缺失则跳过。"""
    draft = load_draft_cards(batch_id)
    state = load_state(batch_id)
    cards_state = state.get("cards", {})
    rows = []
    for dc in draft:
        cid = dc.get("card_id") or dc.get("chunk_id")
        entry = cards_state.get(cid)
        rows.append({
            "card_id": cid,
            "draft": dc,
            "entry": entry,
            "decision": (entry or {}).get("decision", "pending"),
            "edited": bool((entry or {}).get("edited")),
            "effective": effective_card(dc, entry),
        })
    return rows


def summary(batch_id: str) -> dict:
    rows = list_review_rows(batch_id)
    out = {d: 0 for d in DECISIONS}
    for r in rows:
        out[r["decision"]] = out.get(r["decision"], 0) + 1
    out["total"] = len(rows)
    return out


# ─── 写入单卡决定 ─────────────────────────────────────────
def set_decision(batch_id: str, card_id: str, decision: str,
                 edited_card: dict | None = None, review_notes: str = "",
                 reviewer: str = "admin") -> dict:
    if decision not in DECISIONS:
        raise ValueError(f"非法 decision: {decision}")
    state = load_state(batch_id)
    entry = state["cards"].get(card_id, {})
    entry["decision"] = decision
    entry["updated_at"] = _now_iso()
    entry["reviewer"] = reviewer
    entry["review_notes"] = review_notes
    if edited_card is not None:
        entry["card"] = edited_card
        entry["edited"] = True
    else:
        entry.setdefault("edited", False)
    state["cards"][card_id] = entry
    save_state(batch_id, state)
    return entry


def reset_card(batch_id: str, card_id: str) -> None:
    """重置本卡:删除 review 条目,UI 回退到 draft_cards.json 原卡。"""
    state = load_state(batch_id)
    if card_id in state.get("cards", {}):
        del state["cards"][card_id]
        save_state(batch_id, state)


# ─── 导出 approved / rejected ─────────────────────────────
def export_review_outputs(batch_id: str) -> dict:
    """
    依据 review_state 生成:
      approved_cards.json          仅 decision==approved 的生效卡(bare list)
      rejected_cards_reviewed.json 仅 decision==rejected 的卡 + 审核备注
    needs_regenerate / pending 不导出,等待后续处理。
    返回计数汇总。
    """
    rows = list_review_rows(batch_id)
    approved = [r["effective"] for r in rows if r["decision"] == "approved"]
    rejected = [
        {
            "card_id": r["card_id"],
            "review_notes": (r["entry"] or {}).get("review_notes", ""),
            "card": r["effective"],
        }
        for r in rows if r["decision"] == "rejected"
    ]

    _atomic_write_json(approved_cards_path(batch_id), approved)
    _atomic_write_json(rejected_reviewed_path(batch_id), {
        "batch_id": batch_id,
        "generated_at": _now_iso(),
        "count": len(rejected),
        "cards": rejected,
    })
    return {
        "ok": True,
        "approved": len(approved),
        "rejected": len(rejected),
        "pending": sum(1 for r in rows if r["decision"] == "pending"),
        "needs_regenerate": sum(1 for r in rows if r["decision"] == "needs_regenerate"),
        "approved_path": str(approved_cards_path(batch_id)),
    }


# ─── UI 字段解析 helpers(多行文本 ↔ list)─────────────────
def lines_to_list(text: str) -> list[str]:
    return [ln.strip() for ln in (text or "").splitlines() if ln.strip()]


def list_to_lines(value) -> str:
    if isinstance(value, list):
        return "\n".join(str(v) for v in value)
    return str(value or "")


def build_edited_card(draft_card: dict, entry: dict | None, form_values: dict) -> dict:
    """以生效卡为基底,仅覆盖 P1 可编辑字段,其余字段原样保留。"""
    base = dict(effective_card(draft_card, entry))
    for f in EDITABLE_FIELDS:
        if f not in form_values:
            continue
        if f in LIST_FIELDS:
            base[f] = lines_to_list(form_values[f])
        else:
            base[f] = form_values[f]
    return base
