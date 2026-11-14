"""
Style Engine — 风格引擎
负责加载、管理、注入开发者的核心风格DNA到所有AI提示词中
支持：蒸馏生成 style_profile.json、get_style_context()、规则注入

优先级：v1_runtime DNA > 旧 style_profile.json > core_dna.txt > style_rules 表
"""
import json
import os
from pathlib import Path
from config import get_core_dna, save_core_dna, MODEL
from database import get_style_rules, get_cases, get_knowledge, add_style_rule
from services.anthropic_client import get_client

STYLE_PROFILE_PATH = "data/style_profile.json"

# v1_runtime 路径
_ROOT = Path(__file__).resolve().parent.parent
_RUNTIME_DIR = _ROOT / "data" / "developer" / "processed" / "core_dna" / "v1_runtime"


def get_runtime_style_context(stage: str = None, refresh: bool = False) -> dict:
    """
    读取 v1_runtime DNA，返回可注入 prompt 的风格上下文字典。
    stage: 可选关系阶段（stranger/acquaintance/familiar/ambiguous/intimate/conflict/cooling_down/rejection）
    refresh: True 时清除缓存重新读取
    返回：{
        "prompt": str,          # runtime_style_prompt.md 全文
        "profile": dict,        # runtime_style_profile.json
        "reply_rules": list,    # runtime_reply_rules.json
        "forbidden": dict,      # runtime_forbidden_rules.json
        "stage_rules": list,    # 当前阶段的 stage rules（若传入 stage）
        "loaded": bool,
        "source": "v1_runtime" | "fallback",
    }
    """
    cache = _runtime_cache
    if not refresh and cache.get("_loaded"):
        result = dict(cache)
        if stage:
            result["stage_rules"] = cache.get("_all_stage_rules", {}).get(stage, [])
        return result

    result: dict = {
        "prompt": "",
        "profile": {},
        "reply_rules": [],
        "forbidden": {"negative_rules": [], "restricted_boundaries": []},
        "stage_rules": [],
        "loaded": False,
        "source": "fallback",
    }

    if not _RUNTIME_DIR.exists():
        print(f"[StyleEngine] ⚠ v1_runtime 目录不存在：{_RUNTIME_DIR}")
        print("[StyleEngine] 请先运行：python scripts/build_runtime_style_context.py")
        return result

    missing = []
    for fname in ("runtime_style_prompt.md", "runtime_style_profile.json",
                  "runtime_reply_rules.json", "runtime_forbidden_rules.json",
                  "runtime_stage_rules.json"):
        if not (_RUNTIME_DIR / fname).exists():
            missing.append(fname)

    if missing:
        print(f"[StyleEngine] ⚠ v1_runtime 缺少文件：{missing}")
        print("[StyleEngine] 请先运行：python scripts/build_runtime_style_context.py")
        return result

    try:
        result["prompt"] = (_RUNTIME_DIR / "runtime_style_prompt.md").read_text(encoding="utf-8")
        result["profile"] = json.loads(
            (_RUNTIME_DIR / "runtime_style_profile.json").read_text(encoding="utf-8"))
        result["reply_rules"] = json.loads(
            (_RUNTIME_DIR / "runtime_reply_rules.json").read_text(encoding="utf-8"))
        result["forbidden"] = json.loads(
            (_RUNTIME_DIR / "runtime_forbidden_rules.json").read_text(encoding="utf-8"))
        all_stage = json.loads(
            (_RUNTIME_DIR / "runtime_stage_rules.json").read_text(encoding="utf-8"))

        result["loaded"] = True
        result["source"] = "v1_runtime"

        # 写入缓存
        cache.update(result)
        cache["_loaded"] = True
        cache["_all_stage_rules"] = all_stage

        if stage:
            result["stage_rules"] = all_stage.get(stage, [])

    except Exception as e:
        print(f"[StyleEngine] ⚠ 读取 v1_runtime 失败：{e}")

    return result


# 模块级缓存
_runtime_cache: dict = {}


class StyleEngine:

    def get_dna(self) -> str:
        return get_core_dna()

    def get_rules(self, rule_type: str = None) -> list:
        return get_style_rules(rule_type=rule_type, active_only=True)

    def get_style_profile(self) -> dict:
        """读取蒸馏后的结构化风格档案（旧版，兼容保留）"""
        if os.path.exists(STYLE_PROFILE_PATH):
            with open(STYLE_PROFILE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        return {}

    def get_style_context(self, stage: str = None) -> str:
        """
        供所有 agent 调用的统一风格上下文。
        优先级：v1_runtime > 旧 style_profile.json > core_dna.txt > style_rules 表
        stage: 可选关系阶段，传入后会附加对应阶段规则
        """
        # ── 优先使用 v1_runtime ───────────────────────────────
        runtime = get_runtime_style_context(stage=stage)
        if runtime["loaded"]:
            lines = [runtime["prompt"]]

            # 附加阶段规则
            if stage and runtime.get("stage_rules"):
                lines.append(f"\n═══ 当前关系阶段：{stage} ═══")
                for r in runtime["stage_rules"][:3]:
                    lines.append(f"  · {r.get('title','')}：{r.get('content','')[:80]}")
                lines.append("═══════════════════════════════════════")

            # 附加 forbidden 摘要
            neg = runtime["forbidden"].get("negative_rules", [])
            if neg:
                lines.append("\n═══ 禁用模式（任何场景下禁止）═══")
                for r in neg[:8]:
                    lines.append(f"  ✗ {r.get('title','')}")
                lines.append("═══════════════════════════════════════")

            return "\n".join(lines)

        # ── 回退：旧链路 ──────────────────────────────────────
        print("[StyleEngine] ⚠ v1_runtime 未加载，回退到旧风格链路")
        profile = self.get_style_profile()
        dna = self.get_dna()
        rules = self.get_rules()

        lines = ["═══ 开发者风格核心（最高优先级，所有回复必须遵守）═══"]

        if profile:
            if profile.get("mindset_core"):
                lines.append("\n【底层心态】")
                for item in profile["mindset_core"]:
                    lines.append(f"  · {item}")
            if profile.get("language_style"):
                lines.append("\n【语言风格】")
                for item in profile["language_style"]:
                    lines.append(f"  · {item}")
            if profile.get("preferred_patterns"):
                lines.append("\n【偏好模式】")
                for item in profile["preferred_patterns"]:
                    lines.append(f"  · {item}")
            if profile.get("forbidden_patterns"):
                lines.append("\n【绝对禁止】")
                for item in profile["forbidden_patterns"]:
                    lines.append(f"  ✗ {item}")
        elif dna.strip() and "暂未蒸馏" not in dna:
            lines.append(dna.strip())

        if rules:
            lines.append("\n【风格规则】")
            type_prefix = {
                "forbidden": "✗ 禁止",
                "required": "✓ 必须",
                "tone": "→ 语气",
                "mindset": "◆ 心态",
                "language_style": "◇ 表达",
                "preferred_pattern": "✓ 偏好",
                "forbidden_pattern": "✗ 禁止",
            }
            for r in rules:
                prefix = type_prefix.get(r["rule_type"], "·")
                lines.append(f"  {prefix}：{r['rule_content']}")

        lines.append("═══════════════════════════════════════")
        return "\n".join(lines)

    def get_relevant_cases(self, technique_tags: list = None, limit: int = 3) -> str:
        cases = []
        if technique_tags:
            for tag in technique_tags[:2]:
                cases.extend(get_cases(technique_tag=tag, limit=2))
        else:
            cases = get_cases(limit=limit)

        seen = set()
        unique_cases = []
        for c in cases:
            if c["id"] not in seen:
                seen.add(c["id"])
                unique_cases.append(c)

        if not unique_cases:
            return ""

        lines = ["\n═══ 参考案例 ═══"]
        for c in unique_cases[:limit]:
            lines.append(f"【{c['title']}】")
            if c.get("her_message"):
                lines.append(f"  她说：{c['her_message']}")
            lines.append(f"  我回：{c['my_reply']}")
            if c.get("outcome"):
                lines.append(f"  结果：{c['outcome']}")
        return "\n".join(lines)

    def inject(self, base_prompt: str, technique_tags: list = None,
               include_cases: bool = True, stage: str = None) -> str:
        """将风格上下文注入到 base_prompt，返回完整 system prompt"""
        style_ctx = self.get_style_context(stage=stage)
        cases_block = self.get_relevant_cases(technique_tags) if include_cases else ""
        return f"{style_ctx}\n{cases_block}\n\n{base_prompt}"

    def distill_from_materials(self, progress_callback=None) -> dict:
        """
        从知识库素材中蒸馏风格档案
        生成 style_profile.json 并写入 developer_style_rules 表
        返回蒸馏结果 dict
        """
        materials = get_knowledge()
        if not materials:
            return {"error": "知识库为空，请先上传素材"}

        all_content = ""
        for item in materials:
            all_content += f"\n\n【{item['category']}】{item.get('title', '')}\n{item['content']}"

        if progress_callback:
            progress_callback("正在分析素材...")

        client = get_client()

        prompt = f"""你是一个风格分析专家。以下是开发者提供的所有素材（成功案例、理论总结、心得体会）。

请深度分析，提炼出开发者独特的风格体系，严格按以下JSON格式输出，不要任何额外文字：

{{
  "mindset_core": [
    "底层心态规则1（例：不低位讨好，保持自身价值感）",
    "底层心态规则2",
    "底层心态规则3"
  ],
  "language_style": [
    "语言风格特征1（例：短句为主，不长篇大论）",
    "语言风格特征2",
    "语言风格特征3"
  ],
  "preferred_patterns": [
    "偏好的互动模式1（例：轻调侃代替直接表白）",
    "偏好的互动模式2",
    "偏好的互动模式3"
  ],
  "forbidden_patterns": [
    "绝对禁止的行为1（例：连续追问）",
    "绝对禁止的行为2",
    "绝对禁止的行为3"
  ],
  "tone_rules": [
    "语气规则1（例：轻松不油腻）",
    "语气规则2"
  ],
  "escalation_logic": "推进关系的核心逻辑（一段话描述）",
  "signature_style": "标志性风格描述（一段话）"
}}

素材内容：
{all_content[:8000]}"""

        try:
            response = client.messages.create(
                model=MODEL,
                max_tokens=2000,
                messages=[{"role": "user", "content": prompt}]
            )
            raw = response.content[0].text.strip()
            if "```json" in raw:
                raw = raw.split("```json")[1].split("```")[0].strip()
            elif "```" in raw:
                raw = raw.split("```")[1].split("```")[0].strip()

            profile = json.loads(raw)

            # 保存 style_profile.json
            os.makedirs("data", exist_ok=True)
            with open(STYLE_PROFILE_PATH, "w", encoding="utf-8") as f:
                json.dump(profile, f, ensure_ascii=False, indent=2)

            # 同步写入 developer_style_rules 表
            _sync_profile_to_rules(profile)

            # 同时更新 core_dna.txt（文字版摘要）
            dna_text = _profile_to_dna_text(profile)
            save_core_dna(dna_text)

            if progress_callback:
                progress_callback("蒸馏完成")

            return profile

        except json.JSONDecodeError as e:
            return {"error": f"JSON解析失败: {e}"}
        except Exception as e:
            return {"error": str(e)}


def _sync_profile_to_rules(profile: dict):
    """将 style_profile 同步写入 developer_style_rules 表"""
    type_map = {
        "mindset_core": ("mindset", 10),
        "language_style": ("language_style", 9),
        "preferred_patterns": ("preferred_pattern", 8),
        "forbidden_patterns": ("forbidden_pattern", 10),
        "tone_rules": ("tone", 7),
    }
    for field, (rule_type, priority) in type_map.items():
        items = profile.get(field, [])
        if isinstance(items, list):
            for item in items:
                if item.strip():
                    add_style_rule(rule_type, item.strip(), priority, source="distilled")


def _profile_to_dna_text(profile: dict) -> str:
    lines = ["# 开发者风格核心 DNA\n"]
    section_map = [
        ("mindset_core", "## 底层心态"),
        ("language_style", "## 语言风格"),
        ("preferred_patterns", "## 偏好模式"),
        ("forbidden_patterns", "## 禁止行为"),
        ("tone_rules", "## 语气规则"),
    ]
    for field, title in section_map:
        items = profile.get(field, [])
        if items:
            lines.append(title)
            for item in items:
                lines.append(f"- {item}")
            lines.append("")
    if profile.get("escalation_logic"):
        lines.append(f"## 推进逻辑\n{profile['escalation_logic']}\n")
    if profile.get("signature_style"):
        lines.append(f"## 标志性风格\n{profile['signature_style']}\n")
    return "\n".join(lines)


# 单例
_engine = None

def get_style_engine() -> StyleEngine:
    global _engine
    if _engine is None:
        _engine = StyleEngine()
    return _engine
