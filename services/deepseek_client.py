"""
DeepSeek client — 项目内统一的 DeepSeek 调用入口（OpenAI SDK 兼容协议）。
- 不打印 API KEY
- 仅暴露 get_deepseek_client / call_deepseek_json 两个小函数
- 配置统一从 config.get_deepseek_config() 读取
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

# 确保项目根在 sys.path（scripts 直接执行时需要）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from config import get_deepseek_config

_cfg = get_deepseek_config()
DEEPSEEK_API_KEY = _cfg.api_key
DEEPSEEK_MODEL   = _cfg.model
DEEPSEEK_BASE_URL = _cfg.base_url

_client = None


def get_deepseek_client():
    """惰性初始化 OpenAI 客户端（指向 DeepSeek base_url）。缺失 KEY 时抛出。"""
    global _client
    if _client is None:
        cfg = get_deepseek_config()
        if not cfg.api_key:
            raise RuntimeError("DEEPSEEK_API_KEY 未配置（请到「⚙ 设置」页填写并保存，或在 .env 中配置）")
        from openai import OpenAI
        _client = OpenAI(
            api_key=cfg.api_key,
            base_url=cfg.base_url,
            timeout=cfg.timeout,
        )
    return _client


def _strip_codefence(s: str) -> str:
    s = s.strip()
    if "```json" in s:
        s = s.split("```json", 1)[1].split("```", 1)[0].strip()
    elif "```" in s:
        s = s.split("```", 1)[1].split("```", 1)[0].strip()
    return s


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
    """V4 系列默认关闭 thinking，其余模型不传此参数。"""
    if model.startswith("deepseek-v4-"):
        return {"thinking": {"type": "disabled"}}
    return {}


def call_deepseek_json(
    system: str,
    user: str,
    max_tokens: int = 1500,
    temperature: float = 0.2,
    model: str | None = None,
) -> tuple[dict | None, str | None]:
    """调用 DeepSeek，期望返回严格 JSON。返回 (data, error)。"""
    try:
        client = get_deepseek_client()
        effective_model = model or get_deepseek_config().model
        resp = client.chat.completions.create(
            model=effective_model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
            extra_body=_thinking_param(effective_model),
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


# ─────────────────────────────────────────────────────────────────────────────
# interest_score 刻度锚定（单一真相源）
# ─────────────────────────────────────────────────────────────────────────────
# 原先内联在 scripts/test_analyzer_model_compare.py，现抽到此处作为共享常量，
# 供测试脚本与云端 cloud_analyzer 共用同一份文本，消除副本漂移风险。
# 说明：这是「打分刻度说明」，只在显式开启 scale_anchor 时追加到 user prompt 末尾，
# 不属于 agents/analyzer.py 原版 prompt 本体（线上 analyzer 不受影响）。
INTEREST_SCALE_ANCHOR = """
【interest_score 打分刻度（务必用满 0–10 浮点量程，不要压缩到 0–1）】
- 0–1：明显无兴趣/敷衍/回避（单字、话题终结、长时间不回）
- 2–4：礼貌但低投入，被动回应，无主动信号
- 5–6：中性偏正，有来有回但未见明确 IOI
- 7–8：明确高兴趣（主动约见、撒娇、连续主动分享、明显 IOI）
- 9–10：强烈投入、主动推进关系
示例：对方只回“嗯”敷衍 → 约 1；对方主动约你周末并撒娇 → 约 7–8。
注意：这是 10 分制，不是 0–1 概率值；分值必须与你上面的文字判断一致。
"""


def call_deepseek_variant(
    system_prompt: str,
    user_prompt: str,
    model: str,
    thinking_enabled: bool,
) -> dict:
    """参数化 DeepSeek 调用：可指定 model 与是否开思考，并捕获 reasoning token。

    单一真相源：测试脚本（模型对比/稳定性复测）与云端分析层共用此函数，
    保证调用协议、解析逻辑、计时口径完全一致。与原内联实现行为逐字等价：
      - thinking 开关 → extra_body={"thinking": {"type": "enabled"/"disabled"}}
      - 思考模式 max_tokens=4000，否则 3000（思考占额外预算）
      - 解析：_strip_codefence → _clean_json_newlines → json.loads，非 dict 记 error
      - 返回 dict 含 ok/data/error/elapsed/in_tokens/out_tokens/raw_len/reasoning_len

    异常一律捕获为结构化失败结果（不抛出），便于批量评测逐条记录。
    """
    t0 = time.perf_counter()
    try:
        client = get_deepseek_client()
        thinking = {"thinking": {"type": "enabled" if thinking_enabled else "disabled"}}
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": system_prompt},
                      {"role": "user", "content": user_prompt}],
            temperature=0.2,
            max_tokens=4000 if thinking_enabled else 3000,  # 思考占额外预算
            extra_body=thinking,
        )
        elapsed = time.perf_counter() - t0
        msg = resp.choices[0].message
        raw = msg.content or ""
        reasoning = getattr(msg, "reasoning_content", None) or ""
        cleaned = _clean_json_newlines(_strip_codefence(raw))
        try:
            data = json.loads(cleaned)
            err = None if isinstance(data, dict) else "non_dict_response"
            if not isinstance(data, dict):
                data = None
        except json.JSONDecodeError as e:
            data, err = None, f"json_decode_error: {e}"
        usage = getattr(resp, "usage", None)
        return {
            "ok": err is None, "data": data, "error": err, "elapsed": elapsed,
            "in_tokens": getattr(usage, "prompt_tokens", None) if usage else None,
            "out_tokens": getattr(usage, "completion_tokens", None) if usage else None,
            "raw_len": len(raw), "reasoning_len": len(reasoning),
        }
    except Exception as e:
        return {"ok": False, "data": None, "error": f"{type(e).__name__}: {e}",
                "elapsed": time.perf_counter() - t0, "in_tokens": None,
                "out_tokens": None, "raw_len": 0, "reasoning_len": 0}
