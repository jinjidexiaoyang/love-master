"""
统一 Anthropic client 工厂

每次调用实时读取 config.get_anthropic_config()（环境变量 > data/api_config.json > 默认值），
base_url 为空时走官方端点，非空时走第三方中转站。
设置页保存后无需重启即可生效。
"""
import anthropic

from config import get_anthropic_config


def get_client() -> anthropic.Anthropic:
    cfg = get_anthropic_config()
    kwargs = {"api_key": cfg.api_key}
    if cfg.base_url:
        kwargs["base_url"] = cfg.base_url
    return anthropic.Anthropic(**kwargs)
