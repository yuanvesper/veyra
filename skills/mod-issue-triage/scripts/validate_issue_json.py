#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""校验 Veyra JSON 报告（schema_version 1.x），供 mod-issue-triage Skill 使用前检查。

用法：
    python validate_issue_json.py issue.json
    python triage_agent.py crash.log --format json | python validate_issue_json.py -

退出码：0 = 通过；2 = 契约校验失败；1 = 读取或解析失败。

本脚本只使用标准库，不导入 triage_agent.py，方便在只安装了 Skill 的环境中使用。
它检查的是“数据是否可以作为事实输入”，不评估分析质量，也不调用模型。
"""

import argparse
import json
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

SUPPORTED_MAJOR = 1
SCHEMA_HINT = "1.0.0"

REQUIRED_TOP = [
    "schema_version",
    "tool",
    "mode",
    "summary",
    "environment",
    "model_environment",
    "mods",
    "evidence",
    "missing_information",
    "conflicts",
    "warnings",
]

ENVIRONMENT_FIELDS = [
    "game_version",
    "java_version",
    "os",
    "loader",
    "loader_version",
]

SUMMARY_FIELDS = [
    "issue_title",
    "symptoms",
    "reproduction_steps",
    "current_assessment",
    "evidence_refs",
    "disclaimer",
]

EVIDENCE_FIELDS = ["id", "type", "start_line", "end_line", "content", "truncated"]

EVIDENCE_ID = re.compile(r"^E[0-9]+$")


def _is_str_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _check_environment(value: Any, where: str, errors: List[str]) -> None:
    if not isinstance(value, dict):
        errors.append(f"{where} 必须是对象，实际为 {type(value).__name__}")
        return
    for field in ENVIRONMENT_FIELDS:
        if field not in value:
            errors.append(f"{where}.{field} 缺失")
        elif value[field] is not None and not isinstance(value[field], str):
            errors.append(f"{where}.{field} 必须是字符串或 null")


def _check_evidence(value: Any, errors: List[str]) -> List[str]:
    if not isinstance(value, list):
        errors.append("evidence 必须是数组")
        return []
    ids: List[str] = []
    if not value:
        errors.append("evidence 为空：没有证据时不能做证据分析")
    for index, item in enumerate(value):
        where = f"evidence[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{where} 必须是对象")
            continue
        for field in EVIDENCE_FIELDS:
            if field not in item:
                errors.append(f"{where}.{field} 缺失")
        evidence_id = item.get("id")
        if not isinstance(evidence_id, str) or not EVIDENCE_ID.match(evidence_id):
            errors.append(f"{where}.id 不合法：{evidence_id!r}")
        elif evidence_id in ids:
            errors.append(f"{where}.id 重复：{evidence_id}")
        else:
            ids.append(evidence_id)
        if not isinstance(item.get("content", ""), str) or not item.get("content"):
            errors.append(f"{where}.content 必须是非空字符串")
        start = item.get("start_line")
        end = item.get("end_line")
        for name, value2 in (("start_line", start), ("end_line", end)):
            if not isinstance(value2, int) or isinstance(value2, bool) or value2 < 1:
                errors.append(f"{where}.{name} 必须是 >= 1 的整数")
        if (
            isinstance(start, int)
            and isinstance(end, int)
            and not isinstance(start, bool)
            and not isinstance(end, bool)
            and start > end
        ):
            errors.append(f"{where} 行号区间无效：{start} > {end}")
        if not isinstance(item.get("truncated"), bool):
            errors.append(f"{where}.truncated 必须是布尔值")
    return ids


def validate_report(data: Any) -> Tuple[List[str], List[str]]:
    """返回 (errors, warnings)。errors 非空表示不能作为事实输入使用。"""
    errors: List[str] = []
    warnings: List[str] = []

    if not isinstance(data, dict):
        return ["顶层必须是 JSON 对象"], warnings

    for field in REQUIRED_TOP:
        if field not in data:
            errors.append(f"缺少必需字段：{field}")
    for field in data:
        if field not in REQUIRED_TOP:
            warnings.append(f"未知字段（已忽略）：{field}")

    version = data.get("schema_version")
    if not isinstance(version, str):
        errors.append("schema_version 必须是字符串")
    else:
        match = re.match(r"^([0-9]+)\.([0-9]+)\.([0-9]+)$", version)
        if not match:
            errors.append(f"schema_version 格式无效：{version!r}（应形如 {SCHEMA_HINT}）")
        elif int(match.group(1)) != SUPPORTED_MAJOR:
            errors.append(
                f"不支持的 schema_version 主版本：{version}（本 Skill 仅支持 "
                f"{SUPPORTED_MAJOR}.x，请升级 Skill 或使用匹配的 Veyra 版本）"
            )

    tool = data.get("tool")
    if not isinstance(tool, dict):
        errors.append("tool 必须是对象")
    else:
        if not isinstance(tool.get("name"), str) or not tool.get("name"):
            errors.append("tool.name 必须是非空字符串")
        elif tool.get("name") != "Veyra":
            warnings.append(f"tool.name 不是 Veyra：{tool.get('name')!r}")
        if not isinstance(tool.get("version"), str) or not tool.get("version"):
            errors.append("tool.version 必须是非空字符串")

    if data.get("mode") not in ("local", "api"):
        errors.append("mode 必须是 local 或 api")

    summary = data.get("summary")
    if not isinstance(summary, dict):
        errors.append("summary 必须是对象")
    else:
        for field in SUMMARY_FIELDS:
            if field not in summary:
                errors.append(f"summary.{field} 缺失")
        if not isinstance(summary.get("issue_title"), str) or not summary.get("issue_title"):
            errors.append("summary.issue_title 必须是非空字符串")
        for field in ("symptoms", "current_assessment", "disclaimer"):
            if field in summary and not isinstance(summary[field], str):
                errors.append(f"summary.{field} 必须是字符串")
        if not _is_str_list(summary.get("reproduction_steps")):
            errors.append("summary.reproduction_steps 必须是字符串数组")
        if not _is_str_list(summary.get("evidence_refs")):
            errors.append("summary.evidence_refs 必须是字符串数组")
        else:
            for ref in summary["evidence_refs"]:
                if not EVIDENCE_ID.match(ref):
                    errors.append(f"summary.evidence_refs 含非法 ID：{ref!r}")

    _check_environment(data.get("environment"), "environment", errors)

    model_environment = data.get("model_environment")
    if model_environment is not None:
        _check_environment(model_environment, "model_environment", errors)

    mods = data.get("mods")
    if not isinstance(mods, list):
        errors.append("mods 必须是数组")
    else:
        for index, mod in enumerate(mods):
            where = f"mods[{index}]"
            if not isinstance(mod, dict):
                errors.append(f"{where} 必须是对象")
                continue
            for field in ("name", "version"):
                if not isinstance(mod.get(field), str) or not mod.get(field):
                    errors.append(f"{where}.{field} 必须是非空字符串")

    evidence_ids = _check_evidence(data.get("evidence"), errors)
    if isinstance(summary, dict) and _is_str_list(summary.get("evidence_refs")):
        for ref in summary["evidence_refs"]:
            if evidence_ids and ref not in evidence_ids:
                errors.append(
                    f"summary.evidence_refs 引用了不存在的证据：{ref}"
                    "（不能引用 JSON 中不存在的证据 ID）"
                )

    if not _is_str_list(data.get("missing_information")):
        errors.append("missing_information 必须是字符串数组")

    conflicts = data.get("conflicts")
    if not isinstance(conflicts, dict):
        errors.append("conflicts 必须是对象")
    else:
        for field, values in conflicts.items():
            if field not in ENVIRONMENT_FIELDS:
                warnings.append(f"conflicts 含未知环境字段：{field}")
            if not _is_str_list(values) or not values:
                errors.append(f"conflicts.{field} 必须是非空字符串数组")
            environment = data.get("environment")
            if isinstance(environment, dict) and environment.get(field) is not None:
                warnings.append(
                    f"conflicts.{field} 有候选值，但 environment.{field} 非 null；"
                    "按契约冲突字段应为 null"
                )

    if not _is_str_list(data.get("warnings")):
        errors.append("warnings 必须是字符串数组")

    return errors, warnings


def load_report(source: str) -> Tuple[Optional[Any], Optional[str]]:
    try:
        if source == "-":
            text = sys.stdin.read()
        else:
            with open(source, "r", encoding="utf-8") as handle:
                text = handle.read()
    except OSError as exc:
        return None, f"无法读取输入：{exc}"
    try:
        return json.loads(text), None
    except json.JSONDecodeError as exc:
        return None, f"JSON 解析失败：第 {exc.lineno} 行第 {exc.colno} 列：{exc.msg}"


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="validate_issue_json.py",
        description="校验 Veyra JSON 报告是否可作为 mod-issue-triage Skill 的事实输入。",
    )
    parser.add_argument("path", help="Veyra JSON 文件路径；使用 - 表示从 stdin 读取")
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="失败时不打印警告，只打印错误",
    )
    parser.add_argument(
        "--case",
        action="store_true",
        help="输入是 evals/cases 下的用例文件，校验其中的 report 字段",
    )
    args = parser.parse_args(argv)

    data, read_error = load_report(args.path)
    if read_error is not None:
        print(read_error, file=sys.stderr)
        return 1
    if args.case:
        if not isinstance(data, dict) or not isinstance(data.get("report"), dict):
            print("用例文件缺少 report 对象", file=sys.stderr)
            return 2
        data = data["report"]

    errors, warnings = validate_report(data)

    for warning in warnings:
        if not args.quiet:
            print(f"[警告] {warning}", file=sys.stderr)
    for error in errors:
        print(f"[错误] {error}", file=sys.stderr)

    if errors:
        print(
            f"契约校验失败：{len(errors)} 个错误。请重新生成 JSON，不要在这种状态下让 Skill 做分析。",
            file=sys.stderr,
        )
        return 2

    evidence_count = len(data.get("evidence", [])) if isinstance(data, dict) else 0
    print(
        f"契约校验通过：schema_version {data.get('schema_version')}，"
        f"{evidence_count} 条证据，{len(data.get('mods', []))} 个 Mod。"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
