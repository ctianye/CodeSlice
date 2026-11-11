"""Deterministic AST extraction, modelled on graphify's engine.

The design here is deliberately the same shape as graphify's
``extractors/engine.py``: one generic walker driven by a per-language
``LanguageConfig`` that names the tree-sitter node types to look for. Adding a
language is then a data change, not a new extractor, which is what keeps ~5k lines
of engine serving a dozen languages.

Two ideas are copied verbatim because they are what makes the resulting graph
useful rather than decorative:

**Two-pass call resolution.** A single file cannot know what ``helper()`` refers
to - the definition may live anywhere. So pass 1 records *raw* call facts
(``caller``, ``callee name``, receiver type when inferable) without guessing, and
pass 2 resolves them against a project-wide symbol table. Edges that survive pass
2 are genuinely ``EXTRACTED``; anything the second pass has to approximate is
marked ``INFERRED``.

**Comments are first-class.** A docstring or a ``# WHY:`` comment becomes its own
node with a ``rationale_for`` edge to the code it explains. That is what lets the
agent answer "why is this like this" from the author's own words instead of
inventing a reason.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

# Relation names. These are the vocabulary the UI and the agent's tools speak.
REL_CONTAINS = "contains"
REL_IMPORTS = "imports"
REL_IMPORTS_FROM = "imports_from"
REL_CALLS = "calls"
REL_INDIRECT_CALL = "indirect_call"
REL_REFERENCES = "references"
REL_INHERITS = "inherits"
REL_IMPLEMENTS = "implements"
REL_METHOD = "method"
REL_DECLARES = "declares"
REL_RATIONALE_FOR = "rationale_for"
REL_USES = "uses"

CONF_EXTRACTED = "EXTRACTED"
CONF_INFERRED = "INFERRED"
CONF_AMBIGUOUS = "AMBIGUOUS"
CONF_UNRESOLVED = "UNRESOLVED"

# Node kinds. ``rationale`` and ``external`` exist so the UI can style them apart
# from ordinary symbols.
KIND_FILE = "file"
KIND_CLASS = "class"
KIND_FUNCTION = "function"
KIND_MODULE = "module"
KIND_RATIONALE = "rationale"
KIND_EXTERNAL = "external"

# Comment markers that mean "this explains the code below", not "this switches off
# the code below". Matching is case-insensitive and the marker may be followed by a
# colon. graphify uses the same idea; the vocabulary is small on purpose so the
# graph does not fill with commentary nobody asked to capture.
RATIONALE_MARKERS = (
    "NOTE:",
    "WHY:",
    "RATIONALE:",
    "IMPORTANT:",
    "WARNING:",
    "HACK:",
    "TODO:",
    "FIXME:",
    "XXX:",
    "注意:",
    "原因:",
    "说明:",
    "重要:",
)


@dataclass
class LanguageConfig:
    """What the generic walker needs to know about one language.

    ``ts_module`` is imported lazily so a missing grammar only costs that
    language's files, never the whole run.
    """

    name: str
    ts_module: str
    # Node types that open a named scope.
    class_types: frozenset[str] = frozenset()
    function_types: frozenset[str] = frozenset()
    import_types: frozenset[str] = frozenset()
    call_types: frozenset[str] = frozenset()
    # Comment node types that may carry a rationale.
    comment_types: frozenset[str] = frozenset({"comment"})
    # Field holding a declaration's name.
    name_field: str = "name"
    # Field holding a call's callee expression.
    call_function_field: str = "function"
    # Field holding a call's argument list.
    call_arguments_field: str = "arguments"
    # Types that represent ``a.b`` member access.
    accessor_types: frozenset[str] = frozenset()
    accessor_object_field: str = "object"
    accessor_attr_field: str = "attribute"
    # Types whose body contains a docstring as its first statement.
    function_body_field: str = "body"
    # Inheritance: field on the class node holding base classes.
    superclass_field: str = "superclasses"
    # Node types inside the superclass field that name one base.
    superclass_name_types: frozenset[str] = frozenset({"identifier", "attribute"})
    # Language-specific import parser: (node, source) -> list of raw import facts.
    import_parser: Callable[[Any, bytes], list[dict[str, Any]]] | None = None


# --------------------------------------------------------------------------- #
# Node identity
# --------------------------------------------------------------------------- #
def make_node_id(path: str, kind: str, name: str | None, line: int) -> str:
    """Stable, globally unique id for one node.

    The shape mirrors graphify's ``<stem>_<namespace>_<name>`` idea but keeps the
    line number: two same-named methods in one file (overloads, nested classes) are
    distinct definitions and must not collapse into one node that inherits both sets
    of edges. A file node uses ``__file__`` as its "name" so it never collides with
    a symbol that happens to be named like its file.
    """
    stem = path
    safe_name = (name or "__file__").strip()
    return f"{stem}::{kind}::{safe_name}::{int(line)}"


def file_node_id(path: str) -> str:
    return make_node_id(path, KIND_FILE, "__file__", 1)


# --------------------------------------------------------------------------- #
# Extraction result
# --------------------------------------------------------------------------- #
@dataclass
class Extraction:
    """One file's contribution, before project-wide resolution.

    ``raw_calls`` and ``raw_inherits`` are the pass-1 facts that cannot be
    resolved without the whole corpus; ``resolve.py`` consumes them.
    """

    path: str
    nodes: list[dict[str, Any]] = field(default_factory=list)
    edges: list[dict[str, Any]] = field(default_factory=list)
    raw_calls: list[dict[str, Any]] = field(default_factory=list)
    raw_inherits: list[dict[str, Any]] = field(default_factory=list)
    raw_imports: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "nodes": self.nodes,
            "edges": self.edges,
            "raw_calls": self.raw_calls,
            "raw_inherits": self.raw_inherits,
            "raw_imports": self.raw_imports,
        }
        if self.error:
            out["error"] = self.error
        return out
