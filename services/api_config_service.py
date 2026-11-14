"""
API Config Service — 持久化第三方模型 API 连接配置
文件：data/api_config.json
说明：API Key 由用户在「⚙ 设置」页运行时填入，保存在本地磁盘（该文件不入版本控制）。
     不打印、不出现在错误信息、不在 UI 明文展示。

配置优先级：环境变量 > data/api_config.json > 默认值。
统一默认值与实时读取逻辑由 config.get_anthropic_config() / get_deepseek_config() 维护。
"""
import json
import os
from datetime import datetime

_CONFIG_PATH = "data/api_config.json"
_PROVIDERS = ("anthropic", "deepseek")


def _safe_load() -> dict:
    if not os.path.exists(_CONFIG_PATH):
        return {}
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def get_api_config() -> dict:
    """返回 {'anthropic': {...}, 'deepseek': {...}, 'updated_at': str}，key 永不回显。"""
    data = _safe_load()
    out = {"updated_at": data.get("updated_at", "")}
    for p in _PROVIDERS:
        saved = data.get(p) if isinstance(data.get(p), dict) else {}
        out[p] = {
            "base_url": (saved.get("base_url") or "").strip(),
            "model": (saved.get("model") or "").strip(),
            "has_key": bool((saved.get("api_key") or "").strip()),
        }
    return out


def save_provider_config(provider: str, base_url: str, model: str, api_key: str) -> tuple[bool, str]:
    """
    保存单个供应商配置。api_key 留空 = 保留已保存的旧值（避免误清）。
    路径为项目内固定字面量 data/api_config.json，不存在任何用户输入参与路径构造。
    返回 (ok, message)。
    """
    if provider not in _PROVIDERS:
        return False, "未知的供应商"
    base_url = (base_url or "").strip()
    model = (model or "").strip()
    api_key = (api_key or "").strip()

    data = _safe_load()
    if not isinstance(data.get(provider), dict):
        data[provider] = {}
    if base_url:
        data[provider]["base_url"] = base_url
    if model:
        data[provider]["model"] = model
    if api_key:
        data[provider]["api_key"] = api_key
    data["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    payload = json.dumps(data, ensure_ascii=False, indent=2)
    try:
        import pathlib
        cfg_file = pathlib.Path("data") / "api_config.json"   # 固定字面量路径
        pathlib.Path("data").mkdir(parents=True, exist_ok=True)
        cfg_file.write_text(payload, encoding="utf-8")
        return True, "设置已保存（API Key 留空时保留原值）"
    except Exception as e:
        return False, f"保存失败：{type(e).__name__}"


def is_config_file_healthy() -> bool:
    if not os.path.exists(_CONFIG_PATH):
        return True
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return isinstance(data, dict)
    except Exception:
        return False


def test_connection(provider: str, base_url: str = "", model: str = "", api_key: str = "") -> tuple[bool, str]:
    """
    用填写参数发起一次最小真实调用，验证连通性；参数留空时用当前生效配置
    （环境变量 > data/api_config.json > 默认值）。
    不把 api_key 写进任何返回信息。
    """
    base_url = (base_url or "").strip()
    model = (model or "").strip()
    api_key = (api_key or "").strip()

    if provider in _PROVIDERS and not (base_url and model and api_key):
        from config import get_anthropic_config, get_deepseek_config
        eff = get_anthropic_config() if provider == "anthropic" else get_deepseek_config()
        base_url = base_url or getattr(eff, "base_url", "") or ""
        model = model or getattr(eff, "model", "")
        api_key = api_key or getattr(eff, "api_key", "")

    if not api_key:
        return False, "API Key 未配置。请到「⚙ 设置」页填写并保存。"

    try:
        if provider == "deepseek":
            from openai import OpenAI
            client = OpenAI(api_key=api_key, base_url=base_url or "https://api.deepseek.com", timeout=30)
            client.chat.completions.create(
                model=model or "deepseek-chat",
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=5,
                temperature=0,
            )
            return True, f"连接成功（模型 {model or 'deepseek-chat'} 已响应）"
        if provider == "anthropic":
            import anthropic
            from config import _normalize_anthropic_base_url  # noqa: PLC0415
            client = anthropic.Anthropic(
                api_key=api_key,
                base_url=_normalize_anthropic_base_url(base_url) or None,
                timeout=30,
            )
            client.messages.create(
                model=model or "claude-opus-4-5",
                max_tokens=5,
                messages=[{"role": "user", "content": "ping"}],
            )
            return True, f"连接成功（模型 {model or 'claude-opus-4-5'} 已响应）"
        return False, "未知的供应商"
    except Exception as e:
        # 只返回异常类型与简短信息，不回显任何密钥内容
        msg = f"{type(e).__name__}: {e}"
        if api_key and api_key in msg:
            msg = msg.replace(api_key, "***")
        return False, msg
