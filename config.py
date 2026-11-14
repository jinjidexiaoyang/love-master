"""
config.py — 恋爱大师 统一配置入口

所有外部 API、大模型、管理端配置从这里读取。
优先级：环境变量 / .env > data/api_config.json > 安全默认值

data/api_config.json 由应用内「⚙ 设置」页写入，用户无需手改文件；
高级用户也可以继续用 .env / 环境变量（优先级更高）。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# 项目根路径（此文件位于根）
_PROJECT_ROOT = Path(__file__).resolve().parent

# 显式加载 .env，支持 scripts/*.py 直接执行时也能读到环境变量
load_dotenv(_PROJECT_ROOT / ".env", override=False)

# ─── 内部工具 ────────────────────────────────────────────────────────────────

def _mask(val: str) -> str:
    """仅用于状态展示：前3位 + *** + 后3位，或 '(未配置)'。绝不在日志/异常里输出完整值。"""
    v = (val or "").strip()
    if not v:
        return "(未配置)"
    if len(v) <= 8:
        return v[:2] + "***"
    return v[:3] + "***" + v[-3:]


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default)


def _api_json() -> dict:
    """读取「⚙ 设置」页保存的 data/api_config.json。文件不存在或损坏时返回空 dict。"""
    path = _PROJECT_ROOT / "data" / "api_config.json"
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _normalize_anthropic_base_url(raw: str) -> str:
    """
    规整 ANTHROPIC_BASE_URL，避免最终请求出现 /v1/v1/messages。

    Anthropic 官方 SDK 会对每个请求自动追加 /v1/messages。
    因此 base_url 末尾不能带 /v1，否则会被拼成 /v1/v1/messages → 404。

    规则：
      - 去掉首尾空白
      - 去掉末尾所有连续的 /
      - 如果以 /v1 结尾（仅末尾匹配，不影响中间路径），去掉这 3 个字符
      - 再次去掉可能留下的尾部 /
      - 空字符串原样返回（调用方决定是否走官方端点）

    示例：
      https://api.anthropic.com         → https://api.anthropic.com
      https://api.anthropic.com/v1      → https://api.anthropic.com
      https://api.anthropic.com/v1/     → https://api.anthropic.com
      https://xxx.com/                  → https://xxx.com
      https://xxx.com/v1/               → https://xxx.com
      https://xxx.com/proxy/v1          → https://xxx.com/proxy
    """
    if not raw:
        return ""
    s = raw.strip()
    if not s:
        return ""
    while s.endswith("/"):
        s = s[:-1]
    if s.endswith("/v1"):
        s = s[:-3]
    while s.endswith("/"):
        s = s[:-1]
    return s


# ─── Anthropic / Claude ──────────────────────────────────────────────────────

@dataclass
class AnthropicConfig:
    api_key: str
    base_url: str | None        # None → 走官方端点
    model: str
    timeout: float
    max_tokens: int

    def masked_key(self) -> str:
        return _mask(self.api_key)

    def is_configured(self) -> bool:
        return bool(self.api_key)


def get_anthropic_config() -> AnthropicConfig:
    """
    优先级：环境变量 > data/api_config.json > 默认值。
    每次调用实时读取，设置页保存后无需重启即可生效。
    """
    saved = (_api_json().get("anthropic") or {})
    raw_base = _env("ANTHROPIC_BASE_URL", "") or (saved.get("base_url") or "").strip()
    normalized = _normalize_anthropic_base_url(raw_base)
    return AnthropicConfig(
        api_key=_env("ANTHROPIC_API_KEY", "") or (saved.get("api_key") or "").strip(),
        base_url=normalized or None,
        model=_env("ANTHROPIC_MODEL", "") or (saved.get("model") or "").strip() or "claude-opus-4-5",
        timeout=float(_env("ANTHROPIC_TIMEOUT", "120")),
        max_tokens=int(_env("ANTHROPIC_MAX_TOKENS", "2048")),
    )


# ─── DeepSeek ────────────────────────────────────────────────────────────────

@dataclass
class DeepSeekConfig:
    api_key: str
    base_url: str
    model: str
    timeout: float

    def masked_key(self) -> str:
        return _mask(self.api_key)

    def is_configured(self) -> bool:
        return bool(self.api_key)


def get_deepseek_config() -> DeepSeekConfig:
    """
    优先级：环境变量 > data/api_config.json > 默认值。
    每次调用实时读取，设置页保存后无需重启即可生效。
    """
    saved = (_api_json().get("deepseek") or {})
    return DeepSeekConfig(
        api_key=_env("DEEPSEEK_API_KEY", "") or (saved.get("api_key") or "").strip(),
        base_url=_env("DEEPSEEK_BASE_URL", "") or (saved.get("base_url") or "").strip() or "https://api.deepseek.com",
        model=_env("DEEPSEEK_MODEL", "") or (saved.get("model") or "").strip() or "deepseek-chat",
        timeout=float(_env("DEEPSEEK_TIMEOUT", "120")),
    )


# ─── Admin ───────────────────────────────────────────────────────────────────

@dataclass
class AdminConfig:
    token: str

    def is_configured(self) -> bool:
        return bool(self.token)


def get_admin_config() -> AdminConfig:
    return AdminConfig(token=_env("LOVE_MASTER_ADMIN_TOKEN", "").strip())


# ─── 通用请求超时 ─────────────────────────────────────────────────────────────

DEFAULT_REQUEST_TIMEOUT: float = float(_env("DEFAULT_REQUEST_TIMEOUT", "30"))


# ─── 以下为原 config.py 保留字段（向后兼容）────────────────────────────────────

# Anthropic — 旧代码仍可 from config import ANTHROPIC_API_KEY / MODEL
# 注意：这些是模块导入时的快照，运行期应改用 get_anthropic_config() 实时读取。
_anthropic = get_anthropic_config()
ANTHROPIC_API_KEY: str        = _anthropic.api_key
ANTHROPIC_BASE_URL: str | None = _anthropic.base_url
MODEL: str                    = _anthropic.model
MAX_TOKENS: int               = _anthropic.max_tokens

# 数据库路径（锚定项目根，避免依赖启动时的工作目录）
DB_PATH     = str(_PROJECT_ROOT / "data" / "lovemaster.db")
CHROMA_PATH = str(_PROJECT_ROOT / "data" / "chroma_db")

RELATIONSHIP_STAGES = ["陌生人", "认识", "熟人", "暧昧", "上头", "痴迷"]

GAME_TECHNIQUES = [
    "延迟回复", "框架置入", "推拉", "反推", "情绪波动",
    "破费测", "反向废测", "稀缺性制造", "神秘感维持",
    "价值展示", "情绪引导", "弱化需求感"
]

CORE_MINDSET = """
【核心心态暂未蒸馏】
请在「知识库」页面上传你的聊天记录、game理论和撩妹哲学，
然后点击「蒸馏核心心态」按钮，系统将提炼你的专属心态DNA。
"""

CORE_DNA_PATH = str(_PROJECT_ROOT / "data" / "core_dna.txt")


def get_core_dna():
    """读取已蒸馏的核心DNA，如果没有则返回默认框架"""
    if os.path.exists(CORE_DNA_PATH):
        with open(CORE_DNA_PATH, "r", encoding="utf-8") as f:
            return f.read()
    return CORE_MINDSET


def save_core_dna(dna_text):
    """保存蒸馏后的核心DNA"""
    os.makedirs("data", exist_ok=True)
    with open(CORE_DNA_PATH, "w", encoding="utf-8") as f:
        f.write(dna_text)

