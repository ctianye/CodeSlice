"""Tool behaviour: path safety, output clipping, approval previews, command rules."""

from __future__ import annotations

from pathlib import Path

from app.agent import tools


def test_list_files_skips_ignored(project: dict):
    root = Path(project["path"])
    out = tools.run_tool("list_files", {"path": "", "depth": 1}, root, project["id"])
    assert "pkg/" in out
    assert "node_modules" not in out


def test_read_file_numbers_lines(project: dict):
    root = Path(project["path"])
    out = tools.run_tool("read_file", {"path": "pkg/sample.py", "start_line": 9, "end_line": 10}, root, project["id"])
    assert "def bar(self, x):" in out
    assert "pkg/sample.py:9-10" in out


def test_read_file_missing(project: dict):
    out = tools.run_tool("read_file", {"path": "nope.py"}, Path(project["path"]), project["id"])
    assert "文件不存在" in out


def test_read_file_rejects_traversal(project: dict):
    out = tools.run_tool("read_file", {"path": "../../secrets.txt"}, Path(project["path"]), project["id"])
    assert "工具执行失败" in out


def test_read_file_clips_long_range(project: dict):
    root = Path(project["path"])
    big = root / "big.py"
    big.write_text("\n".join(f"x_{i} = {i}" for i in range(2000)), encoding="utf-8")
    out = tools.run_tool("read_file", {"path": "big.py", "start_line": 1, "end_line": 2000}, root, project["id"])
    assert len(out) <= tools.MAX_TOOL_OUTPUT + 200


def test_write_file_is_inside_project(project: dict):
    root = Path(project["path"])
    out = tools.run_tool("write_file", {"path": "pkg/new.py", "content": "value = 42\n"}, root, project["id"])
    assert "已写入" in out
    assert (root / "pkg" / "new.py").read_text(encoding="utf-8") == "value = 42\n"


def test_write_file_refuses_escape(project: dict):
    out = tools.run_tool(
        "write_file",
        {"path": "../outside.py", "content": "x"},
        Path(project["path"]),
        project["id"],
    )
    assert "失败" in out


def test_write_file_reports_diff(project: dict):
    root = Path(project["path"])
    out = tools.run_tool("write_file", {"path": "pkg/sample.py", "content": "changed = True\n"}, root, project["id"])
    assert "-" in out and "+changed = True" in out


def test_run_command_blocks_dangerous():
    assert tools._is_blocked("shutdown /s /t 0")
    assert tools._is_blocked("mkfs.ext4 /dev/sda")
    assert not tools._is_blocked("pytest -q")


def test_run_command_executes_in_project(project: dict):
    import sys

    root = Path(project["path"])
    out = tools.run_tool("run_command", {"command": f'"{sys.executable}" -c "print(1+1)"'}, root, project["id"])
    assert "退出码：0" in out
    assert "2" in out


def test_run_command_blocks_before_running(project: dict):
    out = tools.run_tool("run_command", {"command": "shutdown -h now"}, Path(project["path"]), project["id"])
    assert "已拒绝执行" in out


def test_run_command_rejects_bad_cwd(project: dict):
    out = tools.run_tool("run_command", {"command": "echo hi", "cwd": "../.."}, Path(project["path"]), project["id"])
    assert "工具执行失败" in out


def test_unknown_tool_message(project: dict):
    assert "未知工具" in tools.run_tool("nope", {}, Path(project["path"]), project["id"])


def test_preview_write_file_contains_diff(project: dict):
    preview = tools.preview_for_approval(
        "write_file",
        {"path": "pkg/sample.py", "content": "x = 1\n", "reason": "fix"},
        Path(project["path"]),
    )
    assert preview["path"] == "pkg/sample.py"
    assert preview["reason"] == "fix"
    assert "@@" in preview["diff"] or "---" in preview["diff"]


def test_preview_run_command_fields(project: dict):
    preview = tools.preview_for_approval(
        "run_command",
        {"command": "pytest -q", "cwd": "backend", "reason": "tests"},
        Path(project["path"]),
    )
    assert preview == {"command": "pytest -q", "cwd": "backend", "reason": "tests"}


def test_match_command_rule():
    rules = [
        {"pattern": "pytest *", "needs_confirm": False},
        {"pattern": "git status", "needs_confirm": True},
    ]
    assert tools.match_command_rule("pytest -q", rules) is not None
    assert tools.match_command_rule("git status", rules) is None
    assert tools.match_command_rule("rm -rf x", rules) is None


def test_tool_schemas_are_exposed():
    names = {tool.name for tool in tools.bound_tools()}
    assert names == {
        "list_files",
        "read_file",
        "search_code",
        "git_diff",
        "graph_query",
        "graph_path",
        "graph_neighbors",
        "graph_rationale",
        "write_file",
        "run_command",
    }
    assert {"write_file", "run_command"} <= tools.DANGEROUS_TOOLS
