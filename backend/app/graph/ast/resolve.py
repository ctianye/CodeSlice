"""Pass 2: resolve the raw facts against a project-wide symbol table.

This is the step that turns a bag of per-file observations into a call graph.

The distinction that matters:

- **A call resolved by unique name or by an explicit class qualifier is
  ``EXTRACTED``.** ``Foo.bar()`` names the class, so there is no guessing. So does
  ``helper()`` when exactly one ``helper`` exists in the project.
- **A call that needs a heuristic is ``INFERRED``**, and there are very few of
  those. If a name is ambiguous (three files define ``run``), the edge is dropped
  rather than guessed: a confident wrong caller is worse than a missing one,
  because the user reads the answer as fact.

Order matters. Imports are resolved first because they define the candidate set for
cross-file calls: ``from app.db import get_conn`` tells us which file owns the
``get_conn`` that this file is calling.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from app.graph.ast import (
    CONF_EXTRACTED,
    CONF_INFERRED,
    KIND_CLASS,
    KIND_EXTERNAL,
    KIND_FILE,
    KIND_FUNCTION,
    KIND_MODULE,
    KIND_RATIONALE,
    REL_CALLS,
    REL_IMPORTS,
    REL_IMPORTS_FROM,
    REL_INDIRECT_CALL,
    REL_INHERITS,
    REL_USES,
    Extraction,
    file_node_id,
    make_node_id,
)

# Python builtins that must never resolve to a project symbol even when a project
# happens to define something with the same name.
_BORING_CALLEES = frozenset(
    {
        "print", "len", "str", "int", "float", "bool", "list", "dict", "set", "tuple",
        "range", "enumerate", "zip", "sorted", "reversed", "min", "max", "sum", "abs",
        "isinstance", "issubclass", "getattr", "setattr", "hasattr", "delattr",
        "open", "super", "object", "type", "repr", "hash", "id", "iter", "next",
        "any", "all", "map", "filter", "format", "input", "vars", "dir", "callable",
        "Exception", "ValueError", "TypeError", "KeyError", "IndexError",
        "RuntimeError", "AttributeError", "OSError", "StopIteration",
    }
)


def _module_suffix_variants(module: str) -> list[str]:
    """Path-ish forms an import spec could match.

    ``app.retrieval.hybrid`` should find ``app/retrieval/hybrid.py``. Relative specs
    (``.helpers``, ``./lib/x``) drop their prefix and keep the tail.

    Longer variants are returned first because the resolver prefers the most
    specific match: ``app.core.db`` should find ``app/core/db.py`` rather than some
    other ``db.py``. A package import (``app.retrieval`` with no file of that name)
    is matched by the ``__init__`` variant, which is how Python expresses
    "the package itself".
    """
    if not module:
        return []
    spec = module.strip().strip("\"'")
    if spec.startswith("."):
        spec = spec.lstrip(".")
    spec = spec.replace("\\", "/")
    base = spec if "/" in spec else spec.replace(".", "/")

    out: list[str] = []

    def add(value: str) -> None:
        value = value.strip("/")
        if value and value not in out:
            out.append(value)

    for candidate in (spec, base):
        add(candidate)
        # A package import names a directory, which Python exposes as __init__.
        add(candidate + "/__init__")
        add(candidate + "/__init__.py")
    # Short tails last: they are the fallback when nothing specific matched.
    for candidate in (spec, base):
        tail = candidate.rsplit("/", 1)[-1]
        add(tail)
    return out


def _symbol_key(name: str) -> str:
    return re.sub(r"[^a-z0-9_]", "", name.lower())


def _import_receiver_names(fact: dict[str, Any]) -> list[str]:
    """Names an import makes available as a *receiver* for ``name.attr()`` calls.

    ``import app.services.paths`` binds only ``app``; ``from app import repo`` binds
    ``repo``; ``from app.services import paths`` binds ``paths``. Recording all three
    spellings is what lets ``repo.get_project()`` and ``paths.resolve_root()`` resolve
    without type inference.
    """
    module = str(fact.get("module") or "")
    out: list[str] = []
    if fact.get("kind") == "plain":
        head = module.replace(".", "/").split("/")[0]
        if head:
            out.append(head)
        leaf = module.replace(".", "/").split("/")[-1]
        if leaf and leaf not in out:
            out.append(leaf)
    else:
        leaf = module.replace(".", "/").split("/")[-1]
        if leaf:
            out.append(leaf)
        for name in fact.get("names") or ():
            if name not in out:
                out.append(name)
    return out


class SymbolTable:
    """Index of every named symbol in the project, plus per-file views."""

    def __init__(self, extractions: list[Extraction], root_files: dict[str, str] | None = None):
        # (kind, normalized name) -> [node ids]
        self.by_name: dict[tuple[str, str], list[str]] = defaultdict(list)
        # file path -> [node ids] of the symbols it declares
        self.by_file: dict[str, list[str]] = defaultdict(list)
        # node id -> its record, for fast attribute lookups
        self.nodes: dict[str, dict[str, Any]] = {}
        # file path -> {imported name -> [candidate file paths]}
        self.import_bindings: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
        # file path -> [imported module paths]
        self.imported_modules: dict[str, list[str]] = defaultdict(list)
        # All file paths in the corpus, for suffix matching
        self.files: set[str] = set()

        for extraction in extractions:
            for node in extraction.nodes:
                kind = node.get("kind")
                if kind in (KIND_FILE, KIND_MODULE, KIND_RATIONALE):
                    continue
                self.nodes[node["id"]] = node
                self.by_name[(kind, _symbol_key(node.get("label") or ""))].append(node["id"])
                self.by_file[node["path"]].append(node["id"])
            self.files.add(extraction.path)

        # Suffix -> files, so an import spec can be matched to a path segment.
        self.suffix_index: dict[str, list[str]] = defaultdict(list)
        for path in self.files:
            parts = path.split("/")
            for cut in range(len(parts)):
                key = "/".join(parts[cut:])
                self.suffix_index[key].append(path)
                stem = key.rsplit(".", 1)[0]
                if stem != key:
                    self.suffix_index[stem].append(path)

    # -- lookups -----------------------------------------------------------
    def unique(self, kind: str, name: str) -> str | None:
        """The one node of this kind with that name, or None when absent/ambiguous."""
        ids = self.by_name.get((kind, _symbol_key(name)))
        if not ids:
            return None
        if len(ids) == 1:
            return ids[0]
        # Same-file duplicates are still ambiguous: two `run` in one file means a
        # bare `run()` cannot be attributed without type inference.
        return None

    def candidates(self, kind: str, name: str) -> list[str]:
        return list(self.by_name.get((kind, _symbol_key(name))) or ())

    def methods_of_class(self, class_name: str, method: str) -> str | None:
        """The method node when ``class_name`` is unique and owns exactly one ``method``."""
        class_ids = self.by_name.get((KIND_CLASS, _symbol_key(class_name))) or []
        if len(class_ids) != 1:
            return None
        # Find a function whose parent is that class and whose name matches.
        for node_id, node in self.nodes.items():
            if (
                node.get("kind") == KIND_FUNCTION
                and node.get("parent") == class_ids[0]
                and _symbol_key(node.get("label") or "") == _symbol_key(method)
            ):
                return node_id
        return None

    def resolve_module(self, module: str) -> list[str]:
        """Files an import spec unambiguously identifies.

        The first variant that matches anything wins. Variants are ordered most
        specific first, so ``app.core.db`` resolves to ``app/core/db.py`` instead of
        stopping at an unrelated ``db.py``. When a variant matches more than one
        file the import is genuinely ambiguous and the next variant is tried; if
        nothing is unambiguous the result is empty and the caller records an
        ``external`` node rather than inventing a link.
        """
        for variant in _module_suffix_variants(module):
            candidates = self.suffix_index.get(variant)
            if not candidates:
                continue
            unique = sorted(set(candidates))
            if len(unique) == 1:
                return [unique[0]]
            exact = [c for c in unique if c == variant or c.startswith(variant + "/")]
            if len(exact) == 1:
                return [exact[0]]
        return []


def resolve(extractions: list[Extraction]) -> dict[str, Any]:
    """Merge every extraction and resolve the cross-file facts.

    Returns ``{"nodes", "edges", "stats"}`` ready for clustering.
    """
    table = SymbolTable(extractions)

    nodes: dict[str, dict[str, Any]] = {}
    edges: dict[tuple[str, str, str], dict[str, Any]] = {}

    def add_edge(source: str, target: str, relation: str, confidence: str, line: int | None = None) -> None:
        if source == target or not source or not target:
            return
        key = (source, target, relation)
        existing = edges.get(key)
        if existing is None:
            edges[key] = {
                "source": source,
                "target": target,
                "relation": relation,
                "confidence": confidence,
                "line": line,
            }
        elif confidence == CONF_EXTRACTED and existing["confidence"] != CONF_EXTRACTED:
            # A resolved edge outranks an inferred one between the same pair.
            existing["confidence"] = CONF_EXTRACTED

    # ---- carry over per-file nodes and structural edges ----------------------
    for extraction in extractions:
        for node in extraction.nodes:
            nodes.setdefault(node["id"], node)
        for edge in extraction.edges:
            add_edge(
                edge["source"],
                edge["target"],
                edge["relation"],
                edge.get("confidence", CONF_EXTRACTED),
                edge.get("line"),
            )

    # ---- imports ------------------------------------------------------------
    external_seen: dict[str, str] = {}
    for extraction in extractions:
        path = extraction.path
        source_file = file_node_id(path)
        for fact in extraction.raw_imports:
            module = fact.get("module") or ""
            line = fact.get("line")
            targets = table.resolve_module(module)
            in_corpus = bool(targets)
            if not in_corpus:
                # An import of something we do not index is still a fact about the
                # project: keep one `external` node per module name so "this project
                # depends on X" is answerable. Deduplicated by name.
                #
                # The relation is ``uses``, not ``imports``: an edge count that
                # mixes "depends on fastapi" with "imports app/core/db.py" makes the
                # project's own dependency structure unreadable, because the library
                # edges outnumber the real ones by an order of magnitude.
                key = module or "<unknown>"
                ext_id = external_seen.get(key)
                if ext_id is None:
                    ext_id = make_node_id("<external>", KIND_EXTERNAL, key, 1)
                    external_seen[key] = ext_id
                    nodes.setdefault(
                        ext_id,
                        {
                            "id": ext_id,
                            "label": key,
                            "kind": KIND_EXTERNAL,
                            "path": "",
                            "start_line": 0,
                            "end_line": 0,
                            "parent": None,
                            "external": True,
                        },
                    )
                add_edge(source_file, ext_id, REL_USES, CONF_EXTRACTED, line)
                continue

            relation = REL_IMPORTS_FROM if fact.get("kind") == "from" else REL_IMPORTS
            for target_file in targets:
                add_edge(source_file, file_node_id(target_file), relation, CONF_EXTRACTED, line)
                # Remember the binding so a later ``helper()`` or ``repo.get()`` can
                # find this file. For ``from app import repo`` the imported name is
                # the module alias, so ``repo.x()`` resolves to that file's symbols.
                for name in fact.get("names") or ():
                    table.import_bindings[path][name].append(target_file)
                # ``import app.services.paths`` binds the leading name only, and
                # ``from app.x import y`` where y is itself a module binds y. Record
                # both the leaf module name and the raw spec tail so either spelling
                # of the receiver finds this file.
                bound = _import_receiver_names(fact)
                for name in bound:
                    table.import_bindings[path][name].append(target_file)
                table.imported_modules[path].append(target_file)

            # ``from app.storage import repo`` imports a *submodule*, not a symbol:
            # ``repo`` is app/storage/repo.py and ``repo.get_project()`` must resolve
            # into that file. The rule above binds ``repo`` to app/storage/__init__.py,
            # which declares nothing useful, so each imported name is also tried as a
            # module path of its own.
            if fact.get("kind") == "from":
                base = (fact.get("module") or "").strip()
                for name in fact.get("names") or ():
                    spec = f"{base}.{name}" if base and not base.startswith(".") else f"{base}/{name}"
                    sub_targets = table.resolve_module(spec)
                    for sub_file in sub_targets:
                        if sub_file == target_file:
                            continue
                        add_edge(source_file, file_node_id(sub_file), REL_IMPORTS_FROM, CONF_EXTRACTED, line)
                        table.import_bindings[path][name].append(sub_file)
                        table.imported_modules[path].append(sub_file)

    # ---- inheritance --------------------------------------------------------
    for extraction in extractions:
        for fact in extraction.raw_inherits:
            base_name = fact.get("base") or ""
            # ``Generic[T]`` / ``Base[int]``: the resolvable part is the head.
            head = re.split(r"[\[(<]", base_name, maxsplit=1)[0].strip()
            if not head:
                continue
            target = table.unique(KIND_CLASS, head)
            if target is None:
                # A base class outside the corpus (``BaseModel``, ``TypedDict``)
                # still describes the shape of the project: it says this class is a
                # pydantic model or a typed dict. Recording it as an external node
                # keeps that fact while staying honest that the definition is not
                # here - which is exactly how graphify treats the same edge.
                key = head.rsplit(".", 1)[-1]
                ext_id = external_seen.get(key)
                if ext_id is None:
                    ext_id = make_node_id("<external>", KIND_EXTERNAL, key, 1)
                    external_seen[key] = ext_id
                    nodes.setdefault(
                        ext_id,
                        {
                            "id": ext_id,
                            "label": key,
                            "kind": KIND_EXTERNAL,
                            "path": "",
                            "start_line": 0,
                            "end_line": 0,
                            "parent": None,
                            "external": True,
                        },
                    )
                target = ext_id
            add_edge(fact["child_id"], target, REL_INHERITS, CONF_EXTRACTED, fact.get("line"))

    # ---- calls --------------------------------------------------------------
    # Index declared names per file, so a bare call prefers a local definition.
    #
    # ``calls_dropped`` counts two very different things and must not be read as a
    # quality metric on its own:
    #
    # - a member call on an untyped value (``rows.append()``, ``cfg.get()``). These
    #   are overwhelmingly standard-library methods that have no project definition
    #   to point at, so dropping them is correct, not a miss.
    # - a bare name that is genuinely ambiguous (three files define ``run``). This
    #   one *is* a miss, but guessing would be worse.
    #
    # ``calls_project`` tracks the subset whose callee is actually declared somewhere
    # in the corpus; the resolution rate over that subset is the number worth
    # watching.
    stats = {
        "calls_extracted": 0,
        "calls_inferred": 0,
        "calls_dropped": 0,
        "calls_project": 0,
    }

    def resolve_module_member(path: str, receiver: str, callee: str) -> str | None:
        """Resolve ``module.func()`` where ``module`` is an imported project module.

        This is the dominant cross-file call shape in Python (``repo.get_project()``,
        ``prompts.build_system_prompt()``) and the reason a name-only resolver misses
        so much: the callee lives in a file the caller imports *under an alias*.
        The alias is recorded when the import is resolved, so this is still a proof,
        not a guess: the alias says exactly which file to search.
        """
        modules = table.import_bindings.get(path, {}).get(receiver) or ()
        for target_file in modules:
            for nid in table.by_file.get(target_file, ()):
                node = nodes.get(nid) or {}
                if node.get("kind") not in (KIND_FUNCTION, KIND_CLASS):
                    continue
                if _symbol_key(node.get("label") or "") == _symbol_key(callee):
                    return nid
        # ``import app.services.paths`` binds only the leading name (``app``), so a
        # dotted receiver may not be in the binding map. Fall back to the modules the
        # file imports at all: if exactly one of them declares the callee, that is it.
        hits: list[str] = []
        for target_file in table.imported_modules.get(path, ()):
            for nid in table.by_file.get(target_file, ()):
                node = nodes.get(nid) or {}
                if node.get("kind") not in (KIND_FUNCTION, KIND_CLASS):
                    continue
                if _symbol_key(node.get("label") or "") == _symbol_key(callee):
                    hits.append(nid)
        unique_hits = sorted(set(hits))
        return unique_hits[0] if len(unique_hits) == 1 else None

    for extraction in extractions:
        path = extraction.path
        # A bare call prefers a definition in its own file: that is the nearest
        # scope, and it is what a reader means by "this helper".
        local_symbols: dict[str, str] = {}
        local_counts: dict[str, int] = defaultdict(int)
        for nid in table.by_file.get(path, ()):
            node = nodes.get(nid) or {}
            if node.get("kind") not in (KIND_FUNCTION, KIND_CLASS):
                continue
            key = _symbol_key(node.get("label") or "")
            if not key:
                continue
            local_symbols.setdefault(key, nid)
            local_counts[key] += 1

        for fact in extraction.raw_calls:
            callee = fact.get("callee") or ""
            key = _symbol_key(callee)
            if not key or callee in _BORING_CALLEES:
                continue
            caller_id = fact["caller_id"]
            line = fact.get("line")
            target: str | None = None

            receiver_type = fact.get("receiver_type")
            receiver_name = fact.get("receiver") or ""
            if fact.get("is_member_call"):
                if receiver_type == "<self>":
                    # ``self.method()``: the owning class is the caller's class.
                    owner_class = fact.get("caller_class")
                    if owner_class:
                        target = table.methods_of_class(owner_class, callee)
                elif receiver_type:
                    # ``Foo.bar()``: the receiver is an explicit class name.
                    target = table.methods_of_class(receiver_type, callee)
                if target is None and receiver_name and receiver_name != "self":
                    # ``repo.get_project()``: the receiver is an imported module.
                    target = resolve_module_member(path, receiver_name, callee)
                if target is None and receiver_type:
                    # The receiver was capitalised but is not a known class; it may
                    # still be a module alias (``Prompts.build`` is unlikely, but
                    # ``GraphService.load`` style aliases exist).
                    target = resolve_module_member(path, receiver_name, callee)
                if target is None:
                    # A member call on an untyped value cannot be attributed.
                    stats["calls_dropped"] += 1
                    continue
                add_edge(caller_id, target, REL_CALLS, CONF_EXTRACTED, line)
                stats["calls_extracted"] += 1
                # ``self.method()`` and ``Foo.method()`` both name a project symbol,
                # so they belong in the denominator of the project-call rate.
                stats["calls_project"] += 1
                continue

            # 1. A definition in this file wins: it is the nearest scope.
            if local_counts.get(key) == 1:
                target = local_symbols.get(key)
            # 2. Otherwise an explicit import binding names the file to look in.
            if target is None:
                for imported_file in table.import_bindings.get(path, {}).get(callee, ()):
                    for nid in table.by_file.get(imported_file, ()):
                        node = nodes.get(nid) or {}
                        if (
                            node.get("kind") in (KIND_FUNCTION, KIND_CLASS)
                            and _symbol_key(node.get("label") or "") == key
                        ):
                            if target is None:
                                target = nid
                            else:
                                target = None  # two files both provide it: ambiguous
                                break
                    if target:
                        break
            # 3. Otherwise require global uniqueness.
            if target is None:
                target = table.unique(KIND_FUNCTION, callee) or table.unique(KIND_CLASS, callee)

            if target is None:
                stats["calls_dropped"] += 1
                # Distinguish "no such project symbol" (fine to drop) from
                # "project symbol exists but ambiguous" (a real miss).
                if table.candidates(KIND_FUNCTION, callee) or table.candidates(KIND_CLASS, callee):
                    stats["calls_project"] += 1
                continue
            add_edge(caller_id, target, REL_CALLS, CONF_EXTRACTED, line)
            stats["calls_extracted"] += 1
            stats["calls_project"] += 1

    # ---- indirect calls: a function passed as a value -----------------------
    # ``map(handler)`` / ``submit(process)`` / ``threading.Thread(target=run)``
    # reference a callable without calling it at this site.
    #
    # The engine records this explicitly (``indirect=True``) when it sees a bare
    # identifier sitting in an argument position rather than as the callee of a
    # call. Inferring it from "a call that failed to resolve" would be wrong: an
    # unresolved ordinary call usually means the target is a library method, and
    # labelling that "indirect call" invents a relationship.
    for extraction in extractions:
        for fact in extraction.raw_calls:
            if not fact.get("indirect"):
                continue
            callee = fact.get("callee") or ""
            target = table.unique(KIND_FUNCTION, callee)
            if target and target != fact["caller_id"]:
                add_edge(fact["caller_id"], target, REL_INDIRECT_CALL, CONF_INFERRED, fact.get("line"))

    # ---- final assembly -----------------------------------------------------
    degree: dict[str, int] = defaultdict(int)
    for edge in edges.values():
        degree[edge["source"]] += 1
        degree[edge["target"]] += 1

    node_list = []
    for node in nodes.values():
        node_list.append({**node, "degree": degree.get(node["id"], 0)})
    node_list.sort(key=lambda n: (-n["degree"], n.get("path") or "", n.get("start_line") or 0))

    edge_list = sorted(edges.values(), key=lambda e: (e["source"], e["target"], e["relation"]))

    extracted = sum(1 for e in edge_list if e["confidence"] == CONF_EXTRACTED)
    by_relation: dict[str, int] = defaultdict(int)
    for edge in edge_list:
        by_relation[edge["relation"]] += 1

    return {
        "nodes": node_list,
        "edges": edge_list,
        "stats": {
            "files": len(extractions),
            "symbols": sum(1 for n in node_list if n.get("kind") in (KIND_CLASS, KIND_FUNCTION)),
            "rationale": sum(1 for n in node_list if n.get("kind") == KIND_RATIONALE),
            "external": sum(1 for n in node_list if n.get("kind") == KIND_EXTERNAL),
            "edges": len(edge_list),
            "extracted": extracted,
            "inferred": len(edge_list) - extracted,
            "by_relation": dict(sorted(by_relation.items(), key=lambda kv: -kv[1])),
            **stats,
        },
    }
