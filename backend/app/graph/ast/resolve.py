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


# Languages whose imports are parsed, so a cross-file bare call has to be backed
# by an import binding. The others (Go/Rust/Java/C/C++) keep the global-unique
# fallback because nothing records what their files import.
_GATED_LANGUAGES = frozenset({"python", "javascript", "typescript"})


def _import_local_names(fact: dict[str, Any]) -> list[str]:
    """Names an import actually binds in the importing file.

    ``from x import y as z`` binds only ``z``; the original ``y`` is not
    addressable here. A re-export binds nothing at all: it only records that
    this file depends on another module.
    """
    if fact.get("reexport"):
        return []
    aliases = fact.get("aliases") or {}
    aliased = set(aliases.values())
    names = [name for name in fact.get("names") or () if name not in aliased]
    names.extend(aliases.keys())
    return names


def _import_original_name(fact: dict[str, Any], local: str) -> str:
    return str((fact.get("aliases") or {}).get(local) or local)


def _import_receiver_names(fact: dict[str, Any]) -> list[str]:
    """Names an import makes available as a *receiver* for ``name.attr()`` calls.

    ``import app.services.paths`` binds only ``app``; ``from app import repo`` binds
    ``repo``; ``from app.services import paths`` binds ``paths``; ``import app.x as p``
    binds only ``p``. Recording these spellings is what lets ``repo.get_project()``,
    ``paths.resolve_root()`` and ``p.run()`` resolve without type inference.
    """
    if fact.get("reexport"):
        return []
    module = str(fact.get("module") or "")
    out: list[str] = []
    if fact.get("kind") == "plain":
        aliases = fact.get("aliases") or {}
        if aliases:
            return list(aliases)
        # ``import a.b`` binds only ``a`` in Python. The trailing component is a
        # *module*, not a local name; binding it too made ``paths.x()`` resolve
        # for a file that only wrote ``import app.services.paths``.
        head = module.replace(".", "/").split("/")[0]
        if head:
            out.append(head)
    else:
        # ``from a import b`` binds ``b`` (or its alias), never ``a``.
        for name in _import_local_names(fact):
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
        # file path -> {local alias -> original symbol name}, for `import y as z`
        self.import_originals: dict[str, dict[str, str]] = defaultdict(dict)
        # file path -> [imported module paths]
        self.imported_modules: dict[str, list[str]] = defaultdict(list)
        # file path -> language name, taken from the file node the engine emits
        self.file_languages: dict[str, str] = {}
        # class node id -> [function node ids owned by that exact class node]
        self.class_members: dict[str, list[str]] = defaultdict(list)
        # All file paths in the corpus, for suffix matching
        self.files: set[str] = set()

        for extraction in extractions:
            for node in extraction.nodes:
                kind = node.get("kind")
                if kind in (KIND_FILE, KIND_MODULE, KIND_RATIONALE):
                    if kind == KIND_FILE:
                        self.file_languages[node.get("path") or extraction.path] = node.get("language") or ""
                    continue
                self.nodes[node["id"]] = node
                self.by_name[(kind, _symbol_key(node.get("label") or ""))].append(node["id"])
                self.by_file[node["path"]].append(node["id"])
            self.files.add(extraction.path)

        for node_id, node in self.nodes.items():
            parent = node.get("parent")
            parent_node = self.nodes.get(parent) if parent else None
            if node.get("kind") == KIND_FUNCTION and parent_node and parent_node.get("kind") == KIND_CLASS:
                self.class_members[parent].append(node_id)

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

    def method_of_class(self, class_id: str, method: str) -> str | None:
        """The method owned by this exact class node, with no global name lookup.

        Two classes named ``Panel`` in different files are different classes; using
        the caller's class id keeps ``self.helper()`` from landing in the other one.
        """
        for node_id in self.class_members.get(class_id, ()):
            if _symbol_key(self.nodes[node_id].get("label") or "") == _symbol_key(method):
                return node_id
        return None

    def class_declared_in_file(self, path: str, name: str) -> str | None:
        """A class called ``name`` declared in this file, when there is exactly one."""
        key = _symbol_key(name)
        local = [
            node_id
            for node_id in self.by_file.get(path, ())
            if self.nodes[node_id].get("kind") == KIND_CLASS
            and _symbol_key(self.nodes[node_id].get("label") or "") == key
        ]
        return local[0] if len(local) == 1 else None

    def class_visible_from(self, path: str, name: str) -> str | None:
        """A unique class called ``name`` that the caller file can actually see.

        Visible means declared in the caller's own file or in a file it imports.
        A class elsewhere in the project is not evidence for a ``Foo.bar()`` there.
        """
        local = [
            node_id
            for node_id in self.by_file.get(path, ())
            if self.nodes[node_id].get("kind") == KIND_CLASS
            and _symbol_key(self.nodes[node_id].get("label") or "") == _symbol_key(name)
        ]
        if local:
            return local[0] if len(local) == 1 else None
        imported = set(self.imported_modules.get(path, ()))
        hits = {
            node_id
            for node_id in self.by_name.get((KIND_CLASS, _symbol_key(name)), ())
            if self.nodes[node_id].get("path") in imported
        }
        return hits.pop() if len(hits) == 1 else None

    def language_of(self, path: str) -> str:
        return self.file_languages.get(path) or ""

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
            for local in _import_local_names(fact):
                original = _import_original_name(fact, local)
                if original and original != local:
                    table.import_originals[path][local] = original
            for target_file in targets:
                add_edge(source_file, file_node_id(target_file), relation, CONF_EXTRACTED, line)
                # Remember every *local* name the import binds so a later
                # ``helper()``, ``graph_service.load()`` or ``api.getGraph()`` can
                # find this file. Aliases replace the original name here; binding
                # both would let a call to a name the file never imported resolve.
                bound = _import_receiver_names(fact)
                for name in bound:
                    if target_file not in table.import_bindings[path][name]:
                        table.import_bindings[path][name].append(target_file)
                table.imported_modules[path].append(target_file)

            # ``from app.storage import repo`` imports a *submodule*, not a symbol:
            # ``repo`` is app/storage/repo.py and ``repo.get_project()`` must resolve
            # into that file. The rule above binds ``repo`` to app/storage/__init__.py,
            # which declares nothing useful, so each imported name is also tried as a
            # module path of its own. With ``from app.graph import service as gs`` the
            # submodule spec uses the original name but binds the alias.
            if fact.get("kind") == "from":
                base = (fact.get("module") or "").strip()
                for local in _import_local_names(fact):
                    original = _import_original_name(fact, local)
                    spec = f"{base}.{original}" if base and not base.startswith(".") else f"{base}/{original}"
                    sub_targets = table.resolve_module(spec)
                    for sub_file in sub_targets:
                        if sub_file == target_file:
                            continue
                        add_edge(source_file, file_node_id(sub_file), REL_IMPORTS_FROM, CONF_EXTRACTED, line)
                        if sub_file not in table.import_bindings[path][local]:
                            table.import_bindings[path][local].append(sub_file)
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

    # A bare call prefers a definition in its own file: that is the nearest scope,
    # and it is what a reader means by "this helper".
    local_symbols: dict[str, dict[str, str]] = {}
    local_counts: dict[str, dict[str, int]] = {}
    for path, file_ids in table.by_file.items():
        symbols: dict[str, str] = {}
        counts: dict[str, int] = defaultdict(int)
        for nid in file_ids:
            node = nodes.get(nid) or {}
            if node.get("kind") not in (KIND_FUNCTION, KIND_CLASS):
                continue
            key = _symbol_key(node.get("label") or "")
            if not key:
                continue
            symbols.setdefault(key, nid)
            counts[key] += 1
        local_symbols[path] = symbols
        local_counts[path] = counts

    def resolve_bare(path: str, callee: str) -> str | None:
        """The node a bare name refers to, using only evidence the caller file has.

        Order: a same-file definition, then files this file explicitly imports. A
        unique name elsewhere in the project is *not* evidence for Python/JS/TS: a
        file that never imports ``helper`` cannot be said to call it. Languages
        without an import parser keep the global-unique fallback.
        """
        key = _symbol_key(callee)
        if not key:
            return None
        if local_counts.get(path, {}).get(key) == 1:
            return local_symbols[path][key]
        # An alias imports the symbol under another name in this file:
        # `from app.service import load as load_service` binds `load_service`,
        # while the declaration is still labelled `load`.
        imported_name = table.import_originals.get(path, {}).get(callee, callee)
        imported_key = _symbol_key(imported_name)
        hits: set[str] = set()
        for imported_file in table.import_bindings.get(path, {}).get(callee, ()):
            for nid in table.by_file.get(imported_file, ()):
                node = nodes.get(nid) or {}
                if (
                    node.get("kind") in (KIND_FUNCTION, KIND_CLASS)
                    and _symbol_key(node.get("label") or "") == imported_key
                ):
                    hits.add(nid)
        if hits:
            return hits.pop() if len(hits) == 1 else None
        if table.language_of(path) in _GATED_LANGUAGES:
            return None
        return table.unique(KIND_FUNCTION, imported_name) or table.unique(KIND_CLASS, imported_name)

    for extraction in extractions:
        path = extraction.path
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
                    # ``self``/``this``/``cls``: the owner is the caller's exact
                    # class node, not whichever class happens to share the name.
                    class_id = fact.get("caller_class_id")
                    if not class_id and fact.get("caller_class"):
                        class_id = table.class_declared_in_file(path, fact["caller_class"])
                    if class_id:
                        target = table.method_of_class(class_id, callee)
                elif receiver_type:
                    # ``Foo.bar()``: ``Foo`` must be declared in this file or in a
                    # file this one imports. Elsewhere in the project is not enough.
                    if table.language_of(path) in _GATED_LANGUAGES:
                        class_id = table.class_visible_from(path, receiver_type)
                    else:
                        class_id = table.unique(KIND_CLASS, receiver_type)
                    if class_id:
                        target = table.method_of_class(class_id, callee)
                if target is None and receiver_name and receiver_name not in ("self", "this", "cls"):
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

            target = resolve_bare(path, callee)

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
            target = resolve_bare(extraction.path, callee)
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
