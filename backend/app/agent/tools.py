"""Tools the agent can call.

Every tool receives the project root and refuses to escape it. Two tools have
side effects the user must authorize: ``write_file`` and ``run_command``. The
graph never calls those directly — it asks the approval layer first (see
``app.agent.graph``).

Tools are exposed to the model as LangChain tools so the schema (name / doc /
args) is generated, but execution goes through :func:`run_tool` which returns a
plain string that becomes the ``ToolMessage`` content.
"""

from __future__ import annotations

import contextlib
import difflib
import fnmatch
import shutil
import subprocess
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

from langchain_core.tools import BaseTool, tool

from app.core.errors import AppError
from app.retrieval import hybrid
from app.services import paths, sandbox
from app.services.git_service import diff as git_diff_text

DANGEROUS_TOOLS = {"write_file", "run_command"}

MAX_READ_LINES = 400
MAX_TOOL_OUTPUT = 6000
MAX_WRITE_BYTES = 400_000
COMMAND_TIMEOUT = 120

# The denylist lives in ``app.services.sandbox`` so the integrated terminal and
# this tool cannot drift apart; ``_is_blocked`` stays as the local entry point
# used by the tests.
_BLOCKED_COMMAND_FRAGMENTS = sandbox.BLOCKED_FRAGMENTS


def _clip(text: str, limit: int = MAX_TOOL_OUTPUT) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n…(已截断，共 {len(text)} 字符)"


# --------------------------------------------------------------------------- #
# read-only tools
# --------------------------------------------------------------------------- #
def _list_files(root: Path, rel: str = "", depth: int = 1) -> str:
    depth = max(0, min(int(depth or 1), 3))
    base = paths.resolve_in_project(root, rel)
    if not base.is_dir():
        return f"不是目录：{rel}"
    lines: list[str] = []

    def walk(directory: Path, level: int, prefix: str) -> None:
        try:
            entries = sorted(directory.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        except OSError:
            return
        shown = 0
        for entry in entries:
            if entry.name in paths.DEFAULT_IGNORES or entry.name.startswith(".git"):
                continue
            if shown >= 200:
                lines.append(f"{prefix}…（本层已截断）")
                break
            shown += 1
            suffix = "/" if entry.is_dir() else ""
            lines.append(f"{prefix}{entry.name}{suffix}")
            if entry.is_dir() and level < depth:
                walk(entry, level + 1, prefix + "  ")
        return

    walk(base, 0, "")
    header = f"{rel or '.'} （深度 {depth}）:\n"
    return _clip(header + "\n".join(lines))


def _read_file(root: Path, rel: str, start_line: int | None = None, end_line: int | None = None) -> str:
    path = paths.resolve_in_project(root, rel)
    if not path.is_file():
        return f"文件不存在：{rel}"
    if path.stat().st_size > 2 * 1024 * 1024:
        return f"文件过大（>2MB），请用 search_code 定位后再读：{rel}"
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return f"读取失败：{exc}"
    lines = text.splitlines()
    total = len(lines)
    start = max(1, int(start_line or 1))
    end = int(end_line or min(total, start + 199))
    end = min(end, start + MAX_READ_LINES - 1, total)
    if start > total:
        return f"起始行超出文件长度（共 {total} 行）"
    body = "\n".join(f"{i:>5}│ {lines[i - 1]}" for i in range(start, end + 1))
    return _clip(f"{rel}:{start}-{end}（共 {total} 行）\n{body}")


def _search_code(project_id: int, query: str, top_k: int = 6) -> str:
    chunks = hybrid.search(project_id, query, top_k=max(1, min(int(top_k or 6), 10)))
    if not chunks:
        return f"没有检索到与「{query}」相关的代码（可能尚未建立索引）。"
    blocks = []
    for chunk in chunks:
        header = f"{chunk['path']}:{chunk['start_line']}-{chunk['end_line']}"
        if chunk.get("symbol"):
            header += f" ({chunk['kind']} {chunk['symbol']})"
        blocks.append(f"{header}\n```{chunk.get('language') or ''}\n{chunk['content']}\n```")
    return _clip("\n\n".join(blocks))


def _git_diff(root: Path, rel: str | None = None) -> str:
    text = git_diff_text(root, rel)
    return _clip(text or "（没有改动或不是 git 仓库）")


# --------------------------------------------------------------------------- #
# side-effecting tools (approval-gated)
# --------------------------------------------------------------------------- #
def _write_file(root: Path, rel: str, content: str) -> str:
    target = paths.resolve_in_project(root, rel)
    if len(content.encode("utf-8")) > MAX_WRITE_BYTES:
        return f"内容过大（>{MAX_WRITE_BYTES} 字节），请拆分或改用更小的修改。"
    old = ""
    if target.exists():
        if not target.is_file():
            return f"目标不是文件：{rel}"
        old = target.read_text(encoding="utf-8", errors="replace")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    diff = list(
        difflib.unified_diff(
            old.splitlines(),
            content.splitlines(),
            fromfile=f"a/{rel}",
            tofile=f"b/{rel}",
            lineterm="",
            n=3,
        )
    )
    summary = "\n".join(diff[:200])
    return _clip(f"已写入 {rel}（{len(content)} 字符）\n{summary or '（内容无变化）'}")


def _resolve_cwd(root: Path, cwd: str | None) -> Path:
    return paths.resolve_in_project(root, cwd or "")


def _is_blocked(command: str) -> bool:
    return sandbox.is_blocked(command)


def _run_command(
    root: Path,
    command: str,
    cwd: str | None = None,
    reason: str | None = None,
    project_id: int | None = None,
) -> str:
    if _is_blocked(command):
        return "已拒绝执行：该命令命中内置的危险命令黑名单。"
    workdir = _resolve_cwd(root, cwd)
    if not workdir.is_dir():
        return f"工作目录不存在：{cwd}"
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            shell=True,
            cwd=str(workdir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=COMMAND_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        _record_agent_command(root, project_id, command, workdir, -1, started, f"命令超时（>{COMMAND_TIMEOUT}s）")
        return f"命令超时（>{COMMAND_TIMEOUT}s）：{command}"
    except OSError as exc:
        _record_agent_command(root, project_id, command, workdir, -1, started, f"命令无法启动：{exc}")
        return f"命令无法启动：{exc}"
    out = (completed.stdout or "").strip()
    err = (completed.stderr or "").strip()
    _record_agent_command(root, project_id, command, workdir, completed.returncode, started, f"{out}\n{err}")
    parts = [f"$ {command}", f"退出码：{completed.returncode}"]
    if out:
        parts.append("stdout:\n" + out)
    if err:
        parts.append("stderr:\n" + err)
    return _clip("\n\n".join(parts))


def _record_agent_command(
    root: Path,
    project_id: int | None,
    command: str,
    workdir: Path,
    exit_code: int,
    started: float,
    output: str,
) -> None:
    """Mirror an approved agent command into the terminal history panel.

    Pure convenience: a broken buffer must never change what the tool returns,
    and the history keeps only the tail — the full output already lives in the
    conversation's tool message.
    """
    if project_id is None:
        return
    with contextlib.suppress(Exception):  # history is best effort
        sandbox.record(
            project_id=project_id,
            command=command,
            cwd=sandbox.cwd_label(root, workdir),
            source="agent",
            exit_code=exit_code,
            duration_ms=int((time.monotonic() - started) * 1000),
            output_tail=output,
        )


# --------------------------------------------------------------------------- #
# LangChain tool schemas
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=1)
def tool_specs() -> tuple[BaseTool, ...]:
    """Schemas bound to the model; execution goes through ``run_tool``."""

    @tool
    def list_files(path: str = "", depth: int = 1) -> str:
        """列出项目目录内容。path 是相对项目根的路径，depth 为展开层数（0-3）。"""
        return ""

    @tool
    def read_file(path: str, start_line: int = 1, end_line: int = 200) -> str:
        """读取项目内某文件的指定行范围。返回带行号的内容。"""
        return ""

    @tool
    def search_code(query: str, top_k: int = 6) -> str:
        """在项目索引中做关键词 + 语义混合检索，返回相关代码片段。"""
        return ""

    @tool
    def git_diff(path: str = "") -> str:
        """查看工作区未提交的改动（diff），path 为空表示全项目。"""
        return ""

    @tool
    def write_file(path: str, content: str, reason: str) -> str:
        """写入/覆盖项目内文件，需要用户授权。content 为完整的新文件内容。"""
        return ""

    @tool
    def run_command(command: str, cwd: str = "", reason: str = "") -> str:
        """在项目目录内执行 shell 命令，需要用户授权。"""
        return ""

    return (list_files, read_file, search_code, git_diff, write_file, run_command)


def bound_tools() -> list[BaseTool]:
    return list(tool_specs())


# --------------------------------------------------------------------------- #
# execution
# --------------------------------------------------------------------------- #
def run_tool(name: str, args: dict[str, Any], root: Path, project_id: int) -> str:
    """Execute one tool call and return its textual result."""
    try:
        if name == "list_files":
            return _list_files(root, str(args.get("path") or ""), int(args.get("depth") or 1))
        if name == "read_file":
            return _read_file(
                root,
                str(args["path"]),
                args.get("start_line"),
                args.get("end_line"),
            )
        if name == "search_code":
            return _search_code(project_id, str(args.get("query") or ""), int(args.get("top_k") or 6))
        if name == "git_diff":
            return _git_diff(root, str(args.get("path") or "") or None)
        if name == "write_file":
            return _write_file(root, str(args["path"]), str(args.get("content") or ""))
        if name == "run_command":
            return _run_command(
                root,
                str(args.get("command") or ""),
                str(args.get("cwd") or "") or None,
                str(args.get("reason") or "") or None,
                project_id=project_id,
            )
    except AppError as exc:
        return f"工具执行失败：{exc.detail}"
    except KeyError as exc:
        return f"缺少参数：{exc}"
    except Exception as exc:  # keep the loop alive; the model can recover
        return f"工具执行异常：{type(exc).__name__}: {exc}"
    return f"未知工具：{name}"


def preview_for_approval(name: str, args: dict[str, Any], root: Path) -> dict[str, Any]:
    """Build the payload shown in the approval dialog."""
    if name == "write_file":
        rel = str(args.get("path") or "")
        content = str(args.get("content") or "")
        old = ""
        try:
            target = paths.resolve_in_project(root, rel)
            if target.is_file():
                old = target.read_text(encoding="utf-8", errors="replace")
        except AppError:
            old = ""
        diff = "\n".join(
            difflib.unified_diff(
                old.splitlines(),
                content.splitlines(),
                fromfile=f"a/{rel}",
                tofile=f"b/{rel}",
                lineterm="",
                n=3,
            )
        )
        return {
            "path": rel,
            "diff": _clip(diff, 12000) or "（新文件）",
            "reason": str(args.get("reason") or ""),
            "content_length": len(content),
        }
    if name == "run_command":
        return {
            "command": str(args.get("command") or ""),
            "cwd": str(args.get("cwd") or "."),
            "reason": str(args.get("reason") or ""),
        }
    return {"reason": ""}


def match_rule(value: str, rules: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return an always-allow rule that covers ``value`` (a command or a path).

    A rule is a glob: ``pytest`` covers ``pytest -q``, ``pkg/gen.py`` covers that
    exact file, and ``pkg/*`` covers everything below ``pkg``.
    """
    if not value:
        return None
    for rule in rules:
        if rule.get("needs_confirm"):
            continue
        pattern = str(rule.get("pattern") or "")
        if not pattern:
            continue
        if (
            fnmatch.fnmatch(value, pattern)
            or fnmatch.fnmatch(value, pattern + "*")
            or fnmatch.fnmatch(value, pattern + "/*")
        ):
            return rule
    return None


def match_command_rule(command: str, rules: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return a matching always-allow rule for a shell command, if any."""
    return match_rule(command, rules)


def which_python() -> str | None:
    return shutil.which("python")
