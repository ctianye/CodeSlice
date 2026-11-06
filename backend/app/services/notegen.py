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
MAX_CONVERSATION_CHARS = 12000
MAX_CODE_REFS = 4
MAX_REF_LINES = 40
MAX_REF_CHARS = 1600
# Reasoning models spend part of the output budget on hidden reasoning, so a
# note call is given more room than the user's default and retried once with a
# larger budget if the first attempt reasons itself out of any visible text.
NOTE_MAX_TOKENS = 4096
NOTE_RETRY_MAX_TOKENS = 8192

NOTE_PROMPT = """你在帮一位读者把一次代码讲解整理成可长期保存的阅读笔记。

请严格输出四个 Markdown 二级标题小节，标题文字必须完全一致，顺序也一致：

## 标题
用 6-18 个汉字概括这次讲解的知识点本身，例如「LangGraph 中断后的恢复流程」。
不要出现文件名、路径、行号、反引号，不要以「关于」开头。

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

# Only used to re-title an existing note, so it asks for one line and nothing else.
RETITLE_PROMPT = """你在给一条已经保存的代码阅读笔记重新起标题。

只输出一个标题，6-18 个汉字，概括这条笔记讲的知识点。
不要引号、不要编号、不要解释、不要文件名、不要行号、不要以「关于」开头。

笔记内容：
"""

_SECTION_ORDER = ("知识点", "要点", "何时使用")
_HEADING_RE = re.compile(r"^#{1,6}\s*(.+?)\s*$")
# A title must describe the knowledge, not where it came from: strip anything
# shaped like `foo.py:12-30`, `README.md 第 357-359 行` or a bare `第 N 行`.
_LOCATION_RE = re.compile(
    r"\S*\.[A-Za-z0-9]{1,8}:\d+(?:-\d+)?"
    r"|\S*\.[A-Za-z0-9]{1,8}\s*第\s*\d+(?:\s*[-–~]\s*\d+)?\s*行"
    r"|第\s*\d+(?:\s*[-–~]\s*\d+)?\s*行"
)
_MARKUP_RE = re.compile(r"[#*`>\"'“”‘’]")
# Transcript turn markers (`问：` / `答：`), which are never part of a title.
_TURN_PREFIX_RE = re.compile(r"(?:^|\s)(?:问|答)\s*[：:]\s*")
# Longest backtick run in a snippet, used to size the fence that wraps it.
_FENCE_RUN_RE = re.compile(r"`+")
TITLE_MAX_CHARS = 30
TITLE_MIN_CUT = 12


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


def _clip_title(line: str, limit: int = TITLE_MAX_CHARS) -> str:
    """Cut an over-long title at a word or clause boundary instead of mid-token."""
    if len(line) <= limit:
        return line
    window = line[:limit]
    # Prefer the rightmost natural boundary that still leaves a usable title;
    # landing on "笔记按当前打开的项目隔离，" beats keeping two more words.
    for sep in ("，", ",", "、", "；", ";", "：", " ", "·"):
        cut = window.rfind(sep)
        if cut >= TITLE_MIN_CUT:
            return window[:cut].rstrip(" -—:：·|/、,，.。")
    # No boundary: do not slice an ASCII identifier in half (`/api/note` → `/api/no`).
    end = limit
    while end > TITLE_MIN_CUT and window[end - 1].isascii() and window[end - 1].isalnum():
        end -= 1
    return window[:end].rstrip(" -—:：·|/、,，.。")


def _clean_title(text: str) -> str:
    """Turn model output into a knowledge-point title, or ``""`` if unusable.

    Notes used to be titled after their code range ("关于 README.md 第 357-359
    行"), which says nothing about what was learned. Anything that still carries a
    location is rejected here so the caller can fall back to the prose instead of
    storing a title that is really a file pointer.
    """
    line = (text or "").strip().splitlines()[0].strip() if (text or "").strip() else ""
    if not line:
        return ""
    line = _HEADING_RE.sub(r"\1", line)
    line = _MARKUP_RE.sub("", line)
    line = _LOCATION_RE.sub(" ", line)
    # Collapse the gaps the location strip leaves behind.
    line = " ".join(line.split()).strip(" -—:：·|/、,，.。")
    if not line or line.startswith("关于"):
        return ""
    return _clip_title(line)


def _first_sentence(text: str, limit: int = TITLE_MAX_CHARS) -> str:
    """Fallback title: the opening claim of the knowledge section, trimmed."""
    body = " ".join(_strip_leading_h1(text or "").split())
    # A whole-conversation transcript starts with turn markers; they would make
    # the fallback title read "问：你好 答：你好" instead of describing anything.
    body = _TURN_PREFIX_RE.sub("", body).strip()
    if not body:
        return ""
    for stop in ("。", "！", "？", ".", "!", "?"):
        cut = body.find(stop)
        if 0 < cut <= limit:
            return body[:cut].strip()
    return _clip_title(body)


def _refs_from_citations(conversation: dict[str, Any], citations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Read cited ranges from disk so the note keeps the real snippets."""
    project = repo.get_project(int(conversation["project_id"]))
    if project is None:
        return []
    root = Path(project["path"])
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, int, int]] = set()
    for citation in citations:
        path = str(citation.get("path") or "")
        # Knowledge-base notes are cited as note://<id>; they have no lines on disk,
        # so keeping them here would only render "content could not be read".
        if not path or notes_index.note_id_from_path(path) is not None:
            continue
        start = int(citation.get("start_line") or 1)
        end = int(citation.get("end_line") or start)
        key = (path, start, end)
        if key in seen:
            continue
        if len(out) >= MAX_CODE_REFS:
            break
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
    return _refs_from_citations(conversation, citations)


def _conversation_refs(conversation: dict[str, Any], messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every range cited anywhere in the conversation, in first-seen order."""
    citations: list[dict[str, Any]] = []
    for message in messages:
        citations.extend(message.get("citations") or [])
    if not citations and conversation.get("file_path"):
        citations = [
            {
                "path": conversation["file_path"],
                "start_line": conversation.get("start_line") or 1,
                "end_line": conversation.get("end_line") or 1,
                "symbol": None,
            }
        ]
    return _refs_from_citations(conversation, citations)


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


def _fence_for(code: str, language: str) -> tuple[str, str]:
    """Pick a fence longer than any backtick run inside the snippet.

    A cited Markdown file often contains fences of its own; wrapping it in the
    usual three backticks would end the block early and spill the rest of the
    note into the page as loose paragraphs.
    """
    longest = 3
    for match in _FENCE_RUN_RE.finditer(code):
        longest = max(longest, len(match.group(0)))
    ticks = "`" * (longest + 1)
    return f"{ticks}{language}", ticks


def render_code_section(refs: list[dict[str, Any]]) -> str:
    if not refs:
        return "（这次讲解没有引用具体代码位置。）"
    parts: list[str] = []
    for ref in refs:
        header = f"### {ref['path']}:{ref['start_line']}-{ref['end_line']}"
        if ref.get("symbol"):
            header += f"（{ref['symbol']}）"
        if ref.get("code"):
            opening, closing = _fence_for(str(ref["code"]), _language_for(str(ref["path"])))
            parts.append(f"{header}\n\n{opening}\n{ref['code']}\n{closing}")
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


def _model_sections(
    answer: str,
    conversation: dict[str, Any],
    transcript: bool = False,
) -> tuple[dict[str, str] | None, str | None]:
    """One chat call for the prose sections; returns (sections, error).

    A reasoning model can burn the whole output budget before emitting any
    visible text, which looks like an empty reply with ``finish_reason:
    "length"``. That is worth one retry with a bigger budget: an empty note tells
    the reader nothing, while the second attempt usually succeeds.
    """
    location = _location(conversation)
    if transcript:
        location = f"{location}（整段会话，包含多轮问答）"
    prompt = NOTE_PROMPT.format(location=location)
    user = _truncate(answer, MAX_CONVERSATION_CHARS)

    error: str | None = None
    for budget in (NOTE_MAX_TOKENS, NOTE_RETRY_MAX_TOKENS):
        try:
            model = model_config.build_chat_model(temperature=0.2, max_tokens=budget)
        except Exception as exc:  # ConfigMissing or provider construction error
            return None, str(getattr(exc, "detail", exc))[:300]
        try:
            reply = model.invoke([("system", prompt), ("user", user)])
        except Exception as exc:  # network / provider failure must not lose the note
            return None, f"{type(exc).__name__}: {exc}"[:300]

        content = getattr(reply, "content", "")
        if isinstance(content, list):  # some providers return content parts
            content = "\n".join(str(part) for part in content)
        text = str(content).strip()
        if text:
            sections = split_sections(_strip_leading_h1(text))
            if not any(sections.get(name) for name in _SECTION_ORDER):
                # Model ignored the template: keep its prose as the knowledge section.
                sections = {"知识点": text}
            return sections, None

        meta = getattr(reply, "response_metadata", None) or {}
        usage = meta.get("token_usage") or {}
        error = (
            "模型没有返回内容"
            f"（finish_reason={meta.get('finish_reason')!r}, completion_tokens={usage.get('completion_tokens')}）"
        )
    return None, error


def _resolve_title(
    sections: dict[str, str],
    source: str,
    project: dict[str, Any] | None,
) -> str:
    """Pick a knowledge-point title, falling back to the prose and then the project.

    The conversation title is deliberately not consulted: for a snippet it reads
    "关于 README.md 第 357-359 行", which describes the selection rather than what
    the note teaches.
    """
    from_model = _clean_title(sections.get("标题", ""))
    if from_model:
        return from_model
    from_prose = _clean_title(_first_sentence(sections.get("知识点", "") or source))
    if from_prose:
        return from_prose
    return _default_title(project)


def _store_note(
    conversation: dict[str, Any],
    conversation_id: str,
    title: str,
    content: str,
) -> dict[str, Any]:
    """Create or refresh the single note owned by a conversation."""
    existing = repo.find_note_for_conversation(conversation_id)
    if existing is not None:
        note = repo.update_note(int(existing["id"]), title, content)
    else:
        note = repo.create_note(int(conversation["project_id"]), title, content, conversation_id)
    if note is None:  # pragma: no cover - defensive, update_note only fails if deleted meanwhile
        raise NotFound("note not found")
    return note


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
    refs = _code_refs(conversation, message)

    sections, error = _model_sections(body, conversation)
    summary_failed = sections is None
    if sections is None:
        sections = _fallback_sections(body, refs)

    title = _resolve_title(sections, body, project)
    content = compose(title, sections, refs)
    note = _store_note(conversation, conversation_id, title, content)

    indexed = _index_note(note)
    return {
        **note,
        "summary_failed": summary_failed,
        "summary_error": error,
        "indexed": indexed,
        "refs": len(refs),
    }


def _transcript(messages: list[dict[str, Any]]) -> str:
    """Flatten a conversation into ``问：/答：`` turns the model can summarise."""
    turns: list[str] = []
    for message in messages:
        role = message.get("role")
        if role not in {"user", "assistant"}:
            continue
        body = (message.get("content") or "").strip()
        if not body:
            continue
        turns.append(f"{'问' if role == 'user' else '答'}：{body}")
    return _truncate("\n\n".join(turns), MAX_CONVERSATION_CHARS)


def generate_from_conversation(conversation_id: str) -> dict[str, Any]:
    """Summarise every turn of a conversation into one knowledge-point note.

    A long reading session drifts across files, so a per-answer note misses the
    thread. This folds the whole session into one note while still collecting
    every cited range into ``## 关联代码``.
    """
    conversation = repo.get_conversation(conversation_id)
    if conversation is None:
        raise NotFound("conversation not found")

    messages = [m for m in repo.list_messages(conversation_id) if m.get("role") in {"user", "assistant"}]
    transcript = _transcript(messages)
    if not transcript.strip():
        raise InvalidInput("this conversation has no content to summarise")

    project = repo.get_project(int(conversation["project_id"]))
    refs = _conversation_refs(conversation, messages)

    sections, error = _model_sections(transcript, conversation, transcript=True)
    summary_failed = sections is None
    if sections is None:
        sections = _fallback_sections(transcript, refs)

    title = _resolve_title(sections, transcript, project)
    content = compose(title, sections, refs)
    note = _store_note(conversation, conversation_id, title, content)

    indexed = _index_note(note)
    return {
        **note,
        "summary_failed": summary_failed,
        "summary_error": error,
        "indexed": indexed,
        "refs": len(refs),
    }


def retitle(note_id: int) -> dict[str, Any]:
    """Ask the model for a knowledge-point title and keep the note if it fails.

    Older notes carry titles like "关于 README.md 第 357-359 行"; this rewrites one
    in place. A failed model call must not lose the note, so it reports
    ``retitle_failed`` and leaves the stored title untouched.
    """
    note = repo.get_note(note_id)
    if note is None:
        raise NotFound("note not found")

    body = str(note.get("content_md") or "")[:4000]
    title: str | None = None
    try:
        model = model_config.build_chat_model(temperature=0.2)
        reply = model.invoke([("system", RETITLE_PROMPT), ("user", body)])
        content = getattr(reply, "content", "")
        if isinstance(content, list):
            content = "\n".join(str(part) for part in content)
        title = _clean_title(str(content))
    except Exception:  # any provider problem keeps the existing title
        title = None

    if not title:
        return {**note, "retitle_failed": True, "indexed": note.get("indexed", False)}

    updated = repo.update_note(note_id, title, None)
    if updated is None:  # pragma: no cover - deleted between the two calls
        raise NotFound("note not found")
    indexed = _index_note(updated)
    return {**updated, "retitle_failed": False, "indexed": indexed}


def _default_title(project: dict[str, Any] | None) -> str:
    name = (project or {}).get("name") or "项目"
    return f"{name} · 阅读笔记"


# Titles the app writes before it knows what the conversation is about. They
# describe a location ("关于 README.md 第 357-359 行") or a mode, so the first
# answer replaces them with the knowledge point.
_AUTO_TITLE_RE = re.compile(
    r"^(?:新对话|项目问答|全项目对话|.* · 阅读笔记"
    r"|关于\s*\S+\s*第\s*\d+(?:\s*[-–~]\s*\d+)?\s*行"
    r"|\S+:\d+(?:\s*[-–~]\s*\d+)?)$"
)


def is_auto_title(title: str | None) -> bool:
    """True when the title is a placeholder rather than a knowledge point."""
    return bool(_AUTO_TITLE_RE.match((title or "").strip()))


def title_from_answer(answer: str) -> str:
    """Cheap knowledge-point title taken from the first line of an answer.

    No model call: this runs on every turn, and the note generator already asks
    the model for a better title when the user saves one. It only has to be good
    enough that the sidebar stops saying "关于 README.md 第 357-359 行".
    """
    for line in (answer or "").splitlines():
        text = _MARKUP_RE.sub("", _HEADING_RE.sub(r"\1", line)).strip()
        if len(text) < 4:
            continue
        title = _clean_title(text)
        if title:
            return title
    return ""


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
