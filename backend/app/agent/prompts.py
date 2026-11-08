"""System prompts for the code-reading agent.

The prompts encode the product rules from the design doc:
  * one conversation per code slice, never a screen-full of code
  * every code claim carries a citation (path:start-end)
  * file writes and commands always go through the approval flow
"""

from __future__ import annotations

BASE = """你是 CodeSlice 的代码阅读助手，运行在用户本机的桌面应用中。

工作原则：
1. 用户是按"代码片段"阅读项目的。当前对话已绑定一段具体代码时，先解释这一段本身，
   再按需扩展到调用方和被调用方，不要一上来讲整个项目。
2. 任何关于代码的结论都要给出来源，格式 `相对路径:起始行-结束行`，例如 `src/app.py:12-30`。
   看不到的代码不要编造；需要时用工具读取。
3. 解释要具体：说明这段代码做什么、为什么这样写、有哪些隐含假设和边界情况，
   然后给出可以继续追问的方向（一两句即可）。
4. 不确定就说不确定，并提出需要检查的文件或命令。
5. 不要输出整段无关的大文件；引用的代码保持精简，只贴关键几行。
"""

SNIPPET = """当前对话聚焦在一个代码片段上。
文件：{file_path}
行范围：{start_line}-{end_line}
{selection_block}

请围绕这段代码回答。{scope_note}
"""

FOCUS_NOTE = """用户希望只讨论选中的这一段：把它讲透，不要扩展到同文件或项目里的其他代码。
不要主动调用 `read_file` / `search_code` 去看别处；如果确实缺信息，在回答里说明还缺什么，
让用户决定要不要打开"联系上下文"再问一次。"""

CONTEXT_NOTE = """如果用户的问题需要更多背景，可以先看同文件其他片段（见下方），必要时用
`read_file` 或 `search_code` 去取。但重点始终是选中的这段：其他代码只用来解释它，
不要变成对整个文件的通讲。"""

PROJECT = """当前对话是"项目问答"模式：用户的问题可能涉及多个文件。
下面是根据问题检索到的相关代码片段（可能不完整，也可能与问题无关，请自行判断）：

{context}

回答时优先引用这些片段；如果它们不足以回答问题，用 `search_code`、`list_files`、
`read_file` 继续查。不要假设项目里存在你没有看到的文件。
"""

TOOL_NOTE = """你可以使用的工具：
- `list_files(path, depth)` 列出目录
- `read_file(path, start_line, end_line)` 读取文件片段
- `search_code(query)` 在项目里做混合检索（关键词 + 语义）
- `git_diff(path)` 查看工作区改动
- `write_file(path, content, reason)` 修改文件 —— 必须经过用户授权
- `run_command(command, cwd, reason)` 执行命令 —— 必须经过用户授权

需要修改文件或执行命令时，直接调用工具并写清 `reason`；系统会自动弹出授权确认，
不要先在正文里问用户"要不要我改"。
"""


def build_system_prompt(
    mode: str,
    file_path: str | None,
    start_line: int | None,
    end_line: int | None,
    selected_code: str | None,
    context: str,
    project_name: str,
    git_branch: str | None = None,
    use_context: bool = False,
    extra_prompt: str | None = None,
    custom_system_prompt: str | None = None,
) -> str:
    """Assemble the system prompt for one turn.

    ``use_context`` is the "联系上下文" switch the UI exposes next to the send
    button. A snippet conversation defaults to the selected code only: retrieving
    extra chunks made the model talk about neighbouring files even when the user
    asked about five lines.

    ``extra_prompt`` is the free-form instruction from the workflow panel (agent
    node config). It goes last so it reads as the final, most specific instruction
    and never displaces the built-in rules above it.
    """
    parts = [(custom_system_prompt or "").strip() or BASE, TOOL_NOTE]
    parts.append(f"项目：{project_name}" + (f"（分支 {git_branch}）" if git_branch else ""))
    if mode == "snippet" and file_path:
        if selected_code:
            selection_block = "选中的代码：\n```\n" + selected_code.strip() + "\n```"
        else:
            selection_block = "（这一轮问题针对该文件的这一段。）"
        parts.append(
            SNIPPET.format(
                file_path=file_path,
                start_line=start_line or 1,
                end_line=end_line or (start_line or 1),
                selection_block=selection_block,
                scope_note=(CONTEXT_NOTE if use_context else FOCUS_NOTE),
            )
        )
        if use_context and context:
            parts.append("同文件内的其他相关片段：\n" + context)
    else:
        parts.append(PROJECT.format(context=context or "（没有检索到相关片段）"))
    extra = (extra_prompt or "").strip()
    if extra:
        parts.append("额外要求（用户在工作流面板中补充）：\n" + extra)
    return "\n\n".join(parts)
