# -*- coding: utf-8 -*-
"""YAML 配置验证命令"""
import os
from pathlib import Path
from typing import Dict, List, Any


def _get_base_dir():
    """获取 video-to-text 根目录"""
    return Path(__file__).parent.parent.parent.parent


def run_validate_config(args, base_dir=None):
    """验证 YAML 配置完整性和有效性"""
    if base_dir is None:
        base_dir = _get_base_dir()
    base_dir = Path(base_dir)
    config_dir = base_dir / "config"

    report = {
        "valid": True,
        "errors": [],
        "warnings": [],
        "files_checked": [],
    }

    # 1. 加载并验证 llm_engine_config.yaml
    engine_cfg_path = config_dir / "llm_engine_config.yaml"
    report["files_checked"].append(str(engine_cfg_path))
    engine_cfg = _load_yaml(engine_cfg_path, report)
    if engine_cfg is not None:
        _validate_engine_config(engine_cfg, report)

    # 2. 加载并验证 note_generation_rules.yaml
    rules_cfg_path = config_dir / "note_generation_rules.yaml"
    report["files_checked"].append(str(rules_cfg_path))
    rules_cfg = _load_yaml(rules_cfg_path, report)
    if rules_cfg is not None:
        _validate_rules_config(rules_cfg, report)

    # 打印报告
    _print_report(report)
    return 0 if report["valid"] else 1


def _load_yaml(path: Path, report: Dict) -> Any:
    """加载 YAML 文件，失败时记录错误"""
    if not path.exists():
        report["errors"].append(f"文件不存在: {path}")
        report["valid"] = False
        return None
    try:
        import yaml
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    except yaml.YAMLError as e:
        report["errors"].append(f"YAML 解析错误 ({path.name}): {e}")
        report["valid"] = False
        return None
    except Exception as e:
        report["errors"].append(f"读取失败 ({path.name}): {e}")
        report["valid"] = False
        return None


def _validate_engine_config(cfg: Dict, report: Dict):
    """验证 llm_engine_config.yaml 结构"""
    # 必需顶层字段
    required_top = ["provider", "quality", "paths"]
    for field in required_top:
        if field not in cfg:
            report["errors"].append(f"llm_engine_config.yaml 缺少必需字段: {field}")
            report["valid"] = False

    # provider 配置
    provider = cfg.get("provider", {})
    provider_type = provider.get("type")
    if not provider_type:
        report["errors"].append("provider.type 未设置")
        report["valid"] = False
    elif provider_type not in ("claude", "openai", "local"):
        report["errors"].append(f"provider.type 无效: {provider_type}（应为 claude/openai/local）")
        report["valid"] = False
    else:
        # 检查对应 provider 子配置
        sub_cfg = provider.get(provider_type, {})
        if not sub_cfg:
            report["errors"].append(f"provider.{provider_type} 配置为空")
            report["valid"] = False
        else:
            required_sub = ["model", "max_tokens", "temperature"]
            for field in required_sub:
                if field not in sub_cfg:
                    report["warnings"].append(f"provider.{provider_type} 缺少字段: {field}")

    # quality 配置
    quality = cfg.get("quality", {})
    if quality:
        min_score = quality.get("min_score")
        if min_score is not None:
            if not (0 <= min_score <= 1):
                report["errors"].append(f"quality.min_score 超出范围: {min_score}（应为 0-1）")
                report["valid"] = False
        max_retries = quality.get("max_retries")
        if max_retries is not None:
            if not (0 <= max_retries <= 10):
                report["warnings"].append(f"quality.max_retries 异常值: {max_retries}（建议 0-10）")

    # knowledge_domains 配置
    domains = cfg.get("knowledge_domains", [])
    if domains:
        _validate_knowledge_domains(domains, report)

    # paths 配置
    paths = cfg.get("paths", {})
    if paths:
        required_paths = ["rules", "transcripts_dir", "notes_dir"]
        for field in required_paths:
            if field not in paths:
                report["warnings"].append(f"paths 缺少字段: {field}")


def _validate_knowledge_domains(domains: List[Dict], report: Dict):
    """验证 knowledge_domains 配置"""
    domain_ids = set()
    for i, domain in enumerate(domains):
        prefix = f"knowledge_domains[{i}]"

        # 必需字段
        if "id" not in domain:
            report["errors"].append(f"{prefix} 缺少必需字段: id")
            report["valid"] = False
        else:
            did = domain["id"]
            if did in domain_ids:
                report["errors"].append(f"{prefix} id 重复: {did}")
                report["valid"] = False
            domain_ids.add(did)

        # keywords（match_keywords 或 keywords）
        has_keywords = "match_keywords" in domain or "keywords" in domain
        if not has_keywords and domain.get("id") != "general":
            report["warnings"].append(f"{prefix} 缺少 match_keywords（general 域除外）")

        # exclude_keywords
        if "exclude_keywords" not in domain and domain.get("id") != "general":
            # exclude 是可选的，仅作提示
            pass

        # match_files
        if "match_files" not in domain and domain.get("id") != "general":
            report["warnings"].append(f"{prefix} 缺少 match_files（general 域除外）")

    # 检查是否有 general 兜底域
    if "general" not in domain_ids:
        report["warnings"].append("knowledge_domains 缺少 general 兜底域")


def _validate_rules_config(cfg: Dict, report: Dict):
    """验证 note_generation_rules.yaml 结构"""
    # 必需顶层字段
    if "rules" not in cfg:
        report["errors"].append("note_generation_rules.yaml 缺少必需字段: rules")
        report["valid"] = False
        return

    rules = cfg["rules"]
    if not isinstance(rules, dict):
        report["errors"].append("rules 字段应为字典")
        report["valid"] = False
        return

    # 检查 R1-R12 规则
    expected_rules = [f"R{i}" for i in range(1, 13)]
    rule_ids = set()
    for rule_key, rule_val in rules.items():
        if isinstance(rule_val, dict) and "id" in rule_val:
            rule_ids.add(rule_val["id"])

    for rid in expected_rules:
        if rid not in rule_ids:
            report["warnings"].append(f"rules 中缺少规则: {rid}")

    # 检查致命规则有 severity: fatal
    fatal_rules = ["R1", "R2", "R3"]
    for rule_key, rule_val in rules.items():
        if isinstance(rule_val, dict):
            rid = rule_val.get("id", "")
            severity = rule_val.get("severity", "")
            if rid in fatal_rules and severity != "fatal":
                report["warnings"].append(f"规则 {rid} 应为 severity: fatal（当前: {severity}）")

    # key_concepts
    if "key_concepts" not in cfg:
        report["warnings"].append("note_generation_rules.yaml 缺少 key_concepts（R4 检查需要）")


def _print_report(report: Dict):
    """打印验证报告"""
    print("\n" + "=" * 60)
    print("  NoteForge 配置验证报告")
    print("=" * 60)

    print("\n  检查文件:")
    for f in report["files_checked"]:
        print(f"    - {f}")

    if report["errors"]:
        print(f"\n  错误 ({len(report['errors'])}):")
        for e in report["errors"]:
            print(f"    [ERROR] {e}")

    if report["warnings"]:
        print(f"\n  警告 ({len(report['warnings'])}):")
        for w in report["warnings"]:
            print(f"    [WARN] {w}")

    if not report["errors"] and not report["warnings"]:
        print("\n  所有配置项验证通过，无错误或警告")

    print("\n" + "-" * 60)
    if report["valid"]:
        print("  结果: VALID")
    else:
        print("  结果: INVALID（存在错误，请修复后重试）")
    print("=" * 60)
