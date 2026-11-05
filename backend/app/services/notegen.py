"""Turn one assistant answer into a structured Markdown reading note.

The note is not a free-form summary: every note uses the same template so that
notes stay comparable, greppable and importable elsewhere.

Template (``# 标题`` is the note title, the rest is the body)::

    # <title>

    ## 知识点      what the code does, what the concept is
    ## 关联代码    the exact cited ranges, copied verbatim with their location
    ## 要点        the load-bearing details, assumptions and edge cases
    ## 何时使用    when to reach for this approach (and when not to)

``## 关联代码`` is assembled from the message's own citations rather than from
the model, so a note never invents a line range. The three prose sections come
from one chat call; when that call fails, the original answer is kept as the
knowledge section and the note is marked ``summary_failed`` instead of lost.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from app.core.errors import AppError, InvalidInput, NotFound
from app.models import config as model_config
from app.services import notes_index, paths
from app.storage import repo

MAX_ANSWER_CHARS = 8000
MAX_CODE_REFS = 4
MAX_REF_LINES = 40
MAX_REF_CHARS = 1600

NOTE_PROMPT = """你在帮一位读者把一次代码讲解整理成可长期保存的阅读笔记。

请严格输出三个 Markdown 二级标题小节，标题文字必须完全一致，顺序也一致：

## 知识点
这段代码/这个概念到底在做什么。用 2-4 句话讲清楚机制，不要复述对话过程。

## 要点
值得记住的实现细节、隐含假设、边界情况和容易踩的坑。用 `- ` 列表，3-6 条。

## 何时使用
什么场景应该这样做、什么场景不该这样做。用 2-3 条 `- ` 列表。

硬性要求：
- 不要输出一级标题，不要输出其它小节，不要写开场白或结尾寒暄。
- 不要粘贴大段代码（引用位置会由系统单独填充）。
- 提到具体位置时写成 `相对路径:起始行-结束行`。
- 全文不超过 500 字。

本次对话的位置：{location}
"""

_SECTION_ORDER = ("知识点", "要点", "何时使用")
_HEADING_RE = re.compile(r"^#{1,6}\s*(.+?)\s*$")


def _location(conversation: dict[str, Any]) -> str:
    path = conversation.get("file_path")
    if not path:
        return "整个项目（项目问答）"
    start = conversation.get("start_line")
    end = conversation.get("end_line")
    if start and end:
        return f"{path}:{start}-{end}"
    if start:
        return f"{path}:{start}"
    return str(path)


def _truncate(text: str, limit: int = MAX_ANSWER_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n\n…（回答过长，后面部分已省略）"


def _strip_leading_h1(body: str) -> str:
    """Notes re-add the title as ``# ...``; drop a duplicate one at the top."""
    lines = body.lstrip("\n").splitlines()
    if lines and lines[0].startswith("# "):
        lines = lines[1:]
        while lines and not lines[0].strip():
            lines = lines[1:]
    return "\n".join(lines).strip()


def split_sections(text: str) -> dict[str, str]:
    """Split model output into ``{heading: body}`` on any Markdown heading."""
    sections: dict[str, str] = {}
    current: str | None = None
    buffer: list[str] = []
    for line in text.splitlines():
        match = _HEADING_RE.match(line)
        if match:
            if current is not None:
                sections[current] = "\n".join(buffer).strip()
            current = match.group(1).strip()
            buffer = []
        elif current is not None:
            buffer.append(line)
    if current is not None:
        sections[current] = "\n".join(buffer).strip()
    return sections


def _code_refs(conversation: dict[str, Any], message: dict[str, Any]) -> list[dict[str, Any]]:
    """Read the cited ranges from disk so the note keeps the real snippet."""
    citations = message.get("citations") or []
    if not citations and conversation.get("file_path"):
        citations = [
            {
                "path": conversation["file_path"],
                "start_line": conversation.get("start_line") or 1,
                "end_line": conversation.get("end_line") or 1,
                "symbol": None,
            }
        ]
    project = repo.get_project(int(conversation["project_id"]))
    if project is None:
        return []
    root = Path(project["path"])
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, int, int]] = set()
    for citation in citations[:MAX_CODE_REFS]:
        path = str(citation.get("path") or "")
        start = int(citation.get("start_line") or 1)
        end = int(citation.get("end_line") or start)
        key = (path, start, end)
        if not path or key in seen:
            continue
        seen.add(key)
        snippet = _read_range(root, path, start, end)
        out.append(
            {
                "path": path,
                "start_line": start,
                "end_line": end,
                "symbol": citation.get("symbol"),
                "code": snippet,
            }
        )
    return out


def _read_range(root: Path, rel: str, start: int, end: int) -> str | None:
    try:
        path = paths.resolve_in_project(root, rel)
    except AppError:
        return None
    if not path.is_file():
        return None
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    if start > len(lines):
        return None
    end = min(max(end, start), start + MAX_REF_LINES - 1, len(lines))
    text = "\n".join(lines[start - 1 : end])
    if len(text) > MAX_REF_CHARS:
        text = text[:MAX_REF_CHARS] + "\n…(已截断)"
    return text


def _language_for(path: str) -> str:
    suffix = Path(path).suffix.lower().lstrip(".")
    return {
        "py": "python",
        "ts": "typescript",
        "tsx": "tsx",
        "js": "javascript",
        "jsx": "jsx",
        "go": "go",
        "rs": "rust",
        "java": "java",
        "c": "c",
        "h": "c",
        "cpp": "cpp",
        "cs": "csharp",
        "json": "json",
        "md": "markdown",
        "sh": "bash",
    }.get(suffix, "")


def render_code_section(refs: list[dict[str, Any]]) -> str:
    if not refs:
        return "（这次讲解没有引用具体代码位置。）"
    parts: list[str] = []
    for ref in refs:
        header = f"### {ref['path']}:{ref['start_line']}-{ref['end_line']}"
        if ref.get("symbol"):
            header += f"（{ref['symbol']}）"
        if ref.get("code"):
            fence = _language_for(str(ref["path"]))
            parts.append(f"{header}\n\n```{fence}\n{ref['code']}\n```")
        else:
            parts.append(f"{header}\n\n（引用位置的内容已读取不到，可能已被修改或删除。）")
    return "\n\n".join(parts)


def compose(title: str, sections: dict[str, str], refs: list[dict[str, Any]]) -> str:
    """Assemble the fixed template, filling any missing section."""
    knowledge = sections.get("知识点", "").strip()
    key_points = sections.get("要点", "").strip()
    when = sections.get("何时使用", "").strip()
    parts = [f"# {title}", ""]
    parts.append("## 知识点")
    parts.append(knowledge or "（模型没有给出知识点小结。）")
    parts.append("")
    parts.append("## 关联代码")
    parts.append(render_code_section(refs))
    parts.append("")
    parts.append("## 要点")
    parts.append(key_points or "- （模型没有给出要点。）")
    parts.append("")
    parts.append("## 何时使用")
    parts.append(when or "- （模型没有给出适用场景。）")
    return "\n".join(parts).strip()


def _fallback_sections(answer: str, refs: list[dict[str, Any]]) -> dict[str, str]:
    """Keep the answer itself when the model call fails."""
    bullets = [f"- `{ref['path']}:{ref['start_line']}-{ref['end_line']}`" for ref in refs]
    return {
        "知识点": _strip_leading_h1(answer),
        "要点": "\n".join(bullets) if bullets else "- （原回答未引用具体位置。）",
        "何时使用": "- 这段内容来自原始回答，未能生成结构化小结，请结合上下文理解。",
    }


def _model_sections(answer: str, conversation: dict[str, Any]) -> tuple[dict[str, str] | None, str | None]:
    """One chat call for the prose sections; returns (sections, error)."""
    try:
        model = model_config.build_chat_model(temperature=0.2)
    except Exception as exc:  # ConfigMissing or provider construction error
        return None, str(getattr(exc, "detail", exc))[:300]
    prompt = NOTE_PROMPT.format(location=_location(conversation))
    try:
        reply = model.invoke([("system", prompt), ("user", _truncate(answer))])
    except Exception as exc:  # network / provider failure must not lose the note
        return None, f"{type(exc).__name__}: {exc}"[:300]
    content = getattr(reply, "content", "")
    if isinstance(content, list):  # some providers return content parts
        content = "\n".join(str(part) for part in content)
    text = str(content).strip()
    if not text:
        return None, "模型没有返回笔记内容"
    sections = split_sections(_strip_leading_h1(text))
    if not any(sections.get(name) for name in _SECTION_ORDER):
        # Model ignored the template: keep its prose as the knowledge section.
        sections = {"知识点": text}
    return sections, None


def generate(conversation_id: str, message_id: str) -> dict[str, Any]:
    """Create (or refresh) the note attached to one assistant message."""
    conversation = repo.get_conversation(conversation_id)
    if conversation is None:
        raise NotFound("conversation not found")

    message = repo.get_message(conversation_id, message_id)
    if message is None:
        raise NotFound("message not found")
    if message["role"] != "assistant":
        raise InvalidInput("only assistant messages can be turned into a note")

    body = (message.get("content") or "").strip()
    if not body:
        raise InvalidInput("this message has no content to summarise")

    project = repo.get_project(int(conversation["project_id"]))
    title = (conversation.get("title") or "").strip() or _default_title(project)
    refs = _code_refs(conversation, message)

    sections, error = _model_sections(body, conversation)
    summary_failed = sections is None
    if sections is None:
        sections = _fallback_sections(body, refs)

    content = compose(title, sections, refs)

    existing = repo.find_note_for_conversation(conversation_id)
    if existing is not None:
        note = repo.update_note(int(existing["id"]), title, content)
    else:
        note = repo.create_note(int(conversation["project_id"]), title, content, conversation_id)
    if note is None:  # pragma: no cover - defensive, update_note only fails if deleted meanwhile
        raise NotFound("note not found")

    indexed = _index_note(note)
    return {
        **note,
        "summary_failed": summary_failed,
        "summary_error": error,
        "indexed": indexed,
        "refs": len(refs),
    }


def _default_title(project: dict[str, Any] | None) -> str:
    name = (project or {}).get("name") or "项目"
    return f"{name} 阅读笔记"


def _index_note(note: dict[str, Any]) -> bool:
    """Put the note into the retrieval pipeline; never fail the note over it."""
    try:
        notes_index.sync_note(note)
    except Exception:  # a broken index must not lose the user's note
        import logging

        logging.getLogger(__name__).warning("note index sync failed", exc_info=True)
        return False
    return True


__all__ = ["generate", "compose", "split_sections", "render_code_section", "MAX_ANSWER_CHARS"]
