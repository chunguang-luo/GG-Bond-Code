"""Memory 系统 — 五层记忆架构。

Phase 1: Auto Memory 基础设施（路径解析、分类法、索引管理、目录保证、Prompt 注入）
Phase 2: NEXTCODE.md 多级发现（context/nextcodemd.py）
Phase 3: Session Memory（结构化模板、双阈值触发、Compact 协同）
Phase 4: 后台提取 Agent（闭包状态、互斥、合并模式）
Phase 5: Agent Memory（三种 scope 路径路由）
Phase 6: Relevant Memories（Scan → Select → Load）
Phase 7: Auto Dream（三重门控、记忆巩固）
"""

# Phase 1: Core infrastructure
from .dir import DIR_EXISTS_GUIDANCE, ensure_memory_dir_exists, get_memory_files
from .index import (
    ENTRYPOINT_NAME,
    MAX_ENTRYPOINT_BYTES,
    MAX_ENTRYPOINT_LINES,
    add_index_entry,
    get_entrypoint_path,
    read_index,
    remove_index_entry,
    truncate_entrypoint_content,
    write_index,
)
from .paths import (
    clear_path_cache,
    find_git_root,
    get_auto_mem_path,
    get_memory_base_dir,
    sanitize_path,
)
from .prompt import load_memory_prompt
from .types import (
    MEMORY_TYPES,
    TYPE_META,
    WHAT_NOT_TO_SAVE,
    MemoryFile,
    MemoryType,
    build_frontmatter,
    parse_frontmatter,
)

# Phase 3: Session Memory
from .session_extract import SessionMemoryState
from .session_memory import SessionMemoryManager
from .session_template import (
    DEFAULT_SESSION_MEMORY_TEMPLATE,
    MAX_SECTION_LENGTH,
    MAX_TOTAL_SESSION_MEMORY_TOKENS,
    create_empty_session_memory,
)

# Phase 4: Background extraction
from .extract import execute_extract_memories, init_extract_memories
from .extract_prompt import EXTRACT_MEMORIES_SYSTEM_PROMPT

# Phase 5: Agent Memory
from .agent_memory import get_agent_memory_dir, load_agent_memory_prompt

# Phase 6: Relevant Memories
from .age import memory_age
from .relevant import get_all_surfed_paths, load_relevant_memories
from .scan import MAX_MEMORY_FILES, MemoryHeader, scan_memory_files
from .select import MAX_SELECTED_MEMORIES, select_relevant_memories

# Phase 7: Auto Dream
from .dream import is_dream_gate_open, record_session, run_dream

__all__ = [
    # Phase 1: Core
    "sanitize_path",
    "find_git_root",
    "get_memory_base_dir",
    "get_auto_mem_path",
    "clear_path_cache",
    "MemoryType",
    "MEMORY_TYPES",
    "TYPE_META",
    "WHAT_NOT_TO_SAVE",
    "MemoryFile",
    "parse_frontmatter",
    "build_frontmatter",
    "ENTRYPOINT_NAME",
    "MAX_ENTRYPOINT_LINES",
    "MAX_ENTRYPOINT_BYTES",
    "get_entrypoint_path",
    "read_index",
    "write_index",
    "truncate_entrypoint_content",
    "add_index_entry",
    "remove_index_entry",
    "DIR_EXISTS_GUIDANCE",
    "ensure_memory_dir_exists",
    "get_memory_files",
    "load_memory_prompt",
    # Phase 3: Session Memory
    "SessionMemoryState",
    "SessionMemoryManager",
    "DEFAULT_SESSION_MEMORY_TEMPLATE",
    "MAX_SECTION_LENGTH",
    "MAX_TOTAL_SESSION_MEMORY_TOKENS",
    "create_empty_session_memory",
    # Phase 4: Background extraction
    "execute_extract_memories",
    "init_extract_memories",
    "EXTRACT_MEMORIES_SYSTEM_PROMPT",
    # Phase 5: Agent Memory
    "get_agent_memory_dir",
    "load_agent_memory_prompt",
    # Phase 6: Relevant Memories
    "memory_age",
    "MemoryHeader",
    "MAX_MEMORY_FILES",
    "scan_memory_files",
    "MAX_SELECTED_MEMORIES",
    "select_relevant_memories",
    "load_relevant_memories",
    "get_all_surfed_paths",
    # Phase 7: Auto Dream
    "is_dream_gate_open",
    "record_session",
    "run_dream",
]
