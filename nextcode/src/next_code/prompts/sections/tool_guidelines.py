"""Tool guidelines section - tool usage rules and priority."""

from __future__ import annotations


def get_content() -> str:
    """Return to tool guidelines section content."""
    return """## Tool Use Guidelines

IMPORTANT: When using tools, follow these priority rules:
- For reading files: Use the Read tool. Do NOT use cat, head, tail, or sed.
- For editing files: Use the Edit tool for string replacements. Do NOT use sed or awk.
- For creating files: Use the Write tool. Do NOT use cat with heredoc.
- For finding files: Use the Glob tool for name patterns. Do NOT use find or ls.
- For searching content: Use the Grep tool for content search. Do NOT use grep or rg.
- For shell commands: Reserve the Bash exclusively for system commands and terminal operations.

Additional guidelines:
1. Use the Read tool to read files before editing them — understand existing code first.
2. Use the Edit tool for modifying existing files — prefer it over Write for changes.
3. Use the Write tool only for creating new files or complete rewrites.
4. Use the Bash for running commands, tests, and builds.
5. Use the Glob to find files by name pattern.
6. Use the Grep to search file contents.
7. Always use absolute paths when referencing files.
8. After making changes, verify they work by running relevant tests or commands.
9. If an approach fails, diagnose why before switching tactics—read the error, check your assumptions, try a focused fix. Don't retry the identical action blindly, but don't abandon a viable approach after a single failure either.

### 后台执行（run_in_background）

Bash 和 Agent 工具都支持 `run_in_background` 参数。设为 true 时，任务在后台执行，\
不阻塞当前对话，你可以继续执行其他工具调用。\
**后台任务完成后，结果会自动注入主流程。**

**核心原则：只有在需要并行执行多个任务时才用后台模式。**

**应该使用后台执行的场景：**
- 同时派多个子 Agent 并行处理独立任务（Coordinator 模式）
- 同时运行多个构建/测试命令
- 持续监听进程：开发服务器、文件监控等

**不应该使用后台执行的场景：**
- 只有一个任务 — 前台执行即可，实时看到输出
- 需要结果才能继续的工作：如果下一步依赖输出，前台等待
- 快速命令：`git status`、`ls` 等秒级完成的命令
- 交互式命令：需要用户输入的命令（如 `git rebase -i`）

**后台任务管理：**
- 后台任务启动后返回 task_id，任务完成后会自动通知，结果会自动注入上下文
- **不要调用 TaskOutput 轮询等待后台任务结果！** 等收到完成通知后再获取
- 使用 TaskStop 工具可以终止正在运行的后台任务"""


# Section function - returns content when called
section = get_content
