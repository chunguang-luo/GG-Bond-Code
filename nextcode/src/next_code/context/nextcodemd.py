"""NEXTCODE.md 多级发现与 @include 指令。

从 CWD 向上遍历到文件系统根目录，收集所有 NEXTCODE.md 系列文件。
支持 @include 指令引用其他文件。

加载顺序（从根到 CWD，离 CWD 越近优先级越高）：
1. NEXTCODE.md（Project）
2. .nextcode/NEXTCODE.md（Project）
3. .nextcode/rules/*.md（Project）
4. NEXTCODE.local.md（Local，不提交到版本控制）
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# 支持的文本文件扩展名（@include 安全约束）
TEXT_EXTENSIONS = {
    ".md",
    ".txt",
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".cfg",
    ".ini",
    ".sh",
    ".bash",
    ".zsh",
    ".css",
    ".html",
    ".xml",
    ".sql",
    ".java",
    ".go",
    ".rs",
    ".rb",
    ".php",
    ".c",
    ".cpp",
    ".h",
    ".hpp",
    ".cs",
    ".swift",
    ".kt",
    ".scala",
    ".r",
    ".R",
    ".lua",
    ".pl",
    ".ps1",
    ".bat",
    ".cmd",
    ".dockerfile",
    ".gitignore",
    ".env",
    ".csv",
    ".tsv",
    ".log",
    ".conf",
    ".properties",
    ".gradle",
    ".cmake",
    ".makefile",
    ".mk",
    ".proto",
    ".graphql",
    ".tf",
    ".hcl",
    ".dart",
    ".elm",
    ".ex",
    ".exs",
    ".erl",
    ".hrl",
    ".clj",
    ".cljs",
    ".hs",
    ".ml",
    ".mli",
    ".fs",
    ".fsx",
    ".nim",
    ".zig",
    ".v",
    ".sv",
    ".vhd",
    ".tcl",
    ".m",
    ".mm",
    ".asm",
    ".s",
}

MAX_INCLUDE_DEPTH = 5


@dataclass
class MemoryFileInfo:
    """一个 NEXTCODE.md 系列文件的信息。"""

    path: str
    type: str  # "Project" or "Local"


def get_memory_files(cwd: str) -> list[MemoryFileInfo]:
    """从 CWD 向上遍历，收集所有 NEXTCODE.md 系列文件。

    Returns:
        按"从根到 CWD"顺序排列的文件列表，确保离 CWD 越近优先级越高。
    """
    dirs = []
    current = Path(cwd).resolve()
    while current != current.parent:
        dirs.append(current)
        current = current.parent

    # reverse: 从根到 CWD，确保离 CWD 越近优先级越高
    files = []
    for dir_path in reversed(dirs):
        # NEXTCODE.md
        p = dir_path / "NEXTCODE.md"
        if p.exists():
            files.append(MemoryFileInfo(path=str(p), type="Project"))

        # .nextcode/NEXTCODE.md
        p = dir_path / ".nextcode" / "NEXTCODE.md"
        if p.exists():
            files.append(MemoryFileInfo(path=str(p), type="Project"))

        # .nextcode/rules/*.md
        rules_dir = dir_path / ".nextcode" / "rules"
        if rules_dir.is_dir():
            for rule_file in sorted(rules_dir.glob("*.md")):
                files.append(MemoryFileInfo(path=str(rule_file), type="Project"))

        # NEXTCODE.local.md
        p = dir_path / "NEXTCODE.local.md"
        if p.exists():
            files.append(MemoryFileInfo(path=str(p), type="Local"))

    return files


def load_all_nextcode_md(cwd: str) -> str | None:
    """加载所有 NEXTCODE.md 系列文件，合并为一个字符串。

    Returns:
        合并后的内容，或 None（无文件时）。
    """
    files = get_memory_files(cwd)
    if not files:
        return None

    parts = []
    for info in files:
        try:
            with open(info.path, "r", encoding="utf-8") as f:
                content = f.read().strip()
            if content:
                # 处理 @include 指令
                content = process_includes(content, info.path)
                if content:
                    # 标记来源
                    label = f"<!-- {os.path.basename(info.path)} ({info.type}) -->"
                    parts.append(f"{label}\n{content}")
        except (IOError, OSError):
            logger.debug("Failed to read %s", info.path)
            continue

    if not parts:
        return None

    return "\n\n".join(parts)


def process_includes(content: str, base_path: str, depth: int = 0) -> str:
    """处理 @include 指令。

    支持两种语法：
    - @./relative-path.md 或 @~/absolute-path.md — @ 后直接跟路径
    - @include ./relative-path.md — @include 关键字形式

    安全约束：
    - 只支持文本文件扩展名
    - 循环引用检测 + 深度限制（5 层）
    """
    if depth > MAX_INCLUDE_DEPTH:
        return content

    lines = content.split("\n")
    result = []
    for line in lines:
        stripped = line.strip()

        # 检查是否是 @include 行
        # 排除 @(... 形式（如 Markdown 链接、邮箱等）
        if stripped.startswith("@") and not stripped.startswith("@("):
            # 提取路径：支持 @path 和 @include path 两种形式
            after_at = stripped[1:]
            if after_at.startswith("include "):
                # @include ./path.md 形式
                ref_path = after_at[len("include "):].strip()
            elif after_at and not after_at[0].isspace():
                # @./path.md 或 @~/path.md 形式
                ref_path = after_at
            else:
                result.append(line)
                continue

            # 排除常见的非引用 @ 用法
            if not ref_path or ref_path.startswith("@"):
                result.append(line)
                continue

            # 检查文件名中是否包含空格（排除 @mention 等非路径用法）
            last_segment = ref_path.split("/")[-1].split("\\")[-1]
            if " " in last_segment and not last_segment.startswith('"'):
                result.append(line)
                continue

            resolved = _resolve_include_path(ref_path, base_path)
            if resolved and Path(resolved).suffix.lower() in TEXT_EXTENSIONS:
                included = _read_included_file(resolved, depth + 1)
                if included is not None:
                    result.append(included)
                else:
                    result.append(line)  # 读取失败保留原行
            else:
                result.append(line)  # 不支持的扩展名保留原行
        else:
            result.append(line)

    return "\n".join(result)


def _resolve_include_path(ref_path: str, base_path: str) -> str | None:
    """解析 @include 路径。

    Args:
        ref_path: @ 后面的路径
        base_path: 包含 @include 的文件路径

    Returns:
        解析后的绝对路径，或 None（解析失败时）。
    """
    if ref_path.startswith("~/"):
        # 用户目录引用
        expanded = os.path.expanduser(ref_path)
        if os.path.isfile(expanded):
            return expanded
        return None

    if ref_path.startswith("./") or ref_path.startswith("../") or ref_path.startswith("/"):
        # 相对路径引用
        base_dir = os.path.dirname(base_path)
        resolved = os.path.normpath(os.path.join(base_dir, ref_path))
        if os.path.isfile(resolved):
            return resolved
        return None

    # 裸文件名——相对于当前文件
    base_dir = os.path.dirname(base_path)
    resolved = os.path.normpath(os.path.join(base_dir, ref_path))
    if os.path.isfile(resolved):
        return resolved

    return None


def _read_included_file(filepath: str, depth: int) -> str | None:
    """读取被引用的文件内容。"""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read().strip()
        if content:
            return process_includes(content, filepath, depth)
        return None
    except (IOError, OSError):
        return None
