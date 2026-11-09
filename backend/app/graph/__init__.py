"""Project knowledge graph: build from indexed chunks, cluster, store, query.

The graph is a *derived* structure over the same ``chunks`` rows the retrieval
pipeline already owns. Building it never re-parses source files: the chunker has
already produced one node per function / class / method, and the import lines are
still inside the file's module chunks, so the builder only has to connect them.

Design rules that come from graphify's architecture and are worth keeping:

* Deterministic and LLM-free. Nothing here calls a model, so a rebuild costs no
  tokens and the result is reproducible.
* Every edge carries a confidence label. ``EXTRACTED`` means the relationship is
  literally in the source (a containment, an import line); ``INFERRED`` means it
  was deduced (a call by name). The UI shows the difference, so a guess never
  masquerades as a fact.
* The graph lives on disk as JSON, never in agent state. A few thousand nodes is
  megabytes; putting that in a LangGraph checkpoint would bloat every turn.
"""

from __future__ import annotations

SCHEMA_VERSION = 4
"""Bumped whenever the builder's output shape or rules change.

Version 2 replaced the chunk-derived graph with a real AST pass: docstrings and
marker comments become ``rationale`` nodes, calls are resolved against a
project-wide symbol table instead of by unique name alone, and imports split into
``imports`` / ``imports_from``.

Version 3 adds JSX component calls, arrow/function-expression names, ``new``
constructor calls, exact-class ``self``/``this``/``cls`` resolution, import-gated
cross-file calls for Python/JS/TS, exported-declaration traversal, and import
aliases (``from x import y as z``).

Version 4 stops Python/JS/TS imports from matching an unrelated project file by
their short tail (``langgraph.types`` no longer resolves to ``types.ts``); only
languages without an import parser keep that fallback.

A stored graph with a different version is rebuilt instead of served. Without
this, changing how edges are derived would silently keep serving a graph built by
the old rules, and answers would cite relationships that no longer match the code.
"""

GRAPH_DIRNAME = ".codeslice"
GRAPH_FILENAME = "graph.json"
