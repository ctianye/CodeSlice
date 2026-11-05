"""Language detection and Tree-sitter parser registry.

Grammars come from the per-language ``tree-sitter-*`` wheels so PyInstaller can
bundle them without runtime downloads. Languages without a grammar fall back to
text chunking (see ``chunker``).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

EXT_TO_LANGUAGE: dict[str, str] = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".hh": "cpp",
    ".json": "json",
    ".sh": "bash",
    ".bash": "bash",
    ".md": "markdown",
    ".mdx": "markdown",
    ".css": "css",
    ".html": "html",
    ".yml": "yaml",
    ".yaml": "yaml",
    ".toml": "toml",
    ".sql": "sql",
    ".lua": "lua",
    ".rb": "ruby",
    ".php": "php",
    ".kt": "kotlin",
    ".swift": "swift",
    ".cs": "csharp",
}

TREE_SITTER_LANGUAGES = {
    "python",
    "javascript",
    "typescript",
    "tsx",
    "go",
    "rust",
    "java",
    "c",
    "cpp",
    "json",
    "bash",
}


def detect_language(path: str, content: str = "") -> str:
    suffix = Path(path).suffix.lower()
    if suffix in EXT_TO_LANGUAGE:
        return EXT_TO_LANGUAGE[suffix]
    name = Path(path).name.lower()
    if name in {"dockerfile", "makefile", "cmakelists.txt"}:
        return "text"
    if content.startswith("#!") and "python" in content[:80]:
        return "python"
    if content.startswith("#!") and "node" in content[:80]:
        return "javascript"
    return "text"


@lru_cache(maxsize=32)
def _load_module(language: str) -> Any:
    if language == "python":
        import tree_sitter_python as mod

        return mod
    if language in {"javascript", "jsx"}:
        import tree_sitter_javascript as mod

        return mod
    if language in {"typescript", "tsx"}:
        import tree_sitter_typescript as mod

        return mod
    if language == "go":
        import tree_sitter_go as mod

        return mod
    if language == "rust":
        import tree_sitter_rust as mod

        return mod
    if language == "java":
        import tree_sitter_java as mod

        return mod
    if language == "c":
        import tree_sitter_c as mod

        return mod
    if language == "cpp":
        import tree_sitter_cpp as mod

        return mod
    if language == "json":
        import tree_sitter_json as mod

        return mod
    if language == "bash":
        import tree_sitter_bash as mod

        return mod
    raise KeyError(language)


@lru_cache(maxsize=32)
def get_parser(language: str):
    """Return a configured ``tree_sitter.Parser`` or ``None`` when unsupported."""
    if language not in TREE_SITTER_LANGUAGES:
        return None
    try:
        from tree_sitter import Language, Parser

        mod = _load_module(language)
        if language == "typescript":
            capsule = mod.language_typescript()
        elif language == "tsx":
            capsule = mod.language_tsx()
        else:
            capsule = mod.language()
        return Parser(Language(capsule))
    except Exception:  # pragma: no cover - missing grammar must degrade, not crash
        return None
