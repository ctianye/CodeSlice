"""The generic AST walker: one implementation, driven by ``languages.LanguageConfig``.

This is the piece that turns a source file into nodes plus *unresolved* call facts.

Why unresolved on purpose: a file in isolation cannot say what ``helper()`` means.
The definition may live in a file parsed an hour later, or never (an external
dependency). Guessing here is exactly how a call graph acquires wrong edges that
look authoritative. So pass 1 records what it literally saw - caller, callee name,
and the receiver when the syntax makes it explicit - and ``resolve.py`` decides
what those names refer to once the whole project is on the table.

What this walker produces, in order of how much value it carries:

1. **Scope nodes** (file / class / function) with exact line ranges.
2. **Containment** (``contains`` for file→class→function, ``method`` for
   class→method) read straight off the AST, so it is ``EXTRACTED``.
3. **Rationale nodes** from docstrings and ``# NOTE:/# WHY:`` comments, each with a
   ``rationale_for`` edge to the code it sits in. This is what lets the agent quote
   the author's own reasoning instead of inventing one.
4. **Imports** as raw facts (module + imported names) for the resolver.
5. **Raw call facts** for the resolver.
6. **Inheritance** as raw facts (base class names) for the resolver.
"""

from __future__ import annotations

import re
from typing import Any

from app.graph.ast import (
    CONF_EXTRACTED,
    KIND_CLASS,
    KIND_FILE,
    KIND_FUNCTION,
    KIND_RATIONALE,
    RATIONALE_MARKERS,
    REL_CONTAINS,
    REL_METHOD,
    REL_RATIONALE_FOR,
    Extraction,
    LanguageConfig,
    file_node_id,
    make_node_id,
)

# Rationale bodies are capped: a 200-line docstring is a document, and pulling it
# into the graph would dwarf every real symbol in the display.
MAX_RATIONALE_CHARS = 400

# A comment further than this many lines above a definition is not about it.
MAX_RATIONALE_DISTANCE = 3

# JSX nodes that *use* a component. The closing tag is deliberately absent: it
# repeats the same name and would double every JSX call.
_JSX_CALL_TYPES = frozenset({"jsx_self_closing_element", "jsx_opening_element"})


def _text(node: Any, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _line(node: Any) -> int:
    return node.start_point[0] + 1


def _end_line(node: Any) -> int:
    return node.end_point[0] + 1


def _start_col(node: Any) -> int:
    """1-based column of a node's first character (Monaco's convention)."""
    return node.start_point[1] + 1


def _end_col(node: Any) -> int:
    """1-based column just past a node's last character."""
    return node.end_point[1] + 1


def _span(node: Any) -> dict[str, int]:
    """Name span in the same 1-based line/column shape the API returns."""
    return {
        "name_line": _line(node),
        "name_col": _start_col(node),
        "name_end_line": _end_line(node),
        "name_end_col": _end_col(node),
    }


def _signature(node: Any, source: bytes) -> str:
    """First line of a declaration, for the editor hover card."""
    text = _text(node, source).splitlines()[0].strip()
    return text[:200]


def _named_child(node: Any, field: str | None) -> Any | None:
    if not field:
        return None
    return node.child_by_field_name(field)


def _walk_descendants(node: Any):
    """Depth-first over named children, iterative so a deep tree cannot blow the stack."""
    stack = [node]
    while stack:
        current = stack.pop()
        yield current
        for child in reversed(getattr(current, "children", [])):
            if getattr(child, "is_named", False):
                stack.append(child)


def _strip_string_delimiters(raw: str) -> str:
    """Turn a Python/JS string literal into its text.

    Tree-sitter gives the literal including quotes and any ``r``/``f``/``b``
    prefix; the graph wants the sentence. Handles triple quotes and the common
    single-line forms, which covers docstrings in practice.
    """
    text = raw.strip()
    # Drop a leading prefix like r"" / f"" / rb""
    text = re.sub(r"^[rRbBuUfF]{0,2}", "", text)
    for quote in ('"""', "'''", '"', "'", "`"):
        if text.startswith(quote) and text.endswith(quote) and len(text) >= 2 * len(quote):
            text = text[len(quote) : -len(quote)]
            break
    # Collapse the indentation tree-sitter preserves inside triple-quoted blocks.
    lines = [line.strip() for line in text.splitlines()]
    joined = " ".join(line for line in lines if line)
    return joined.strip()


def _first_docstring(func_or_class: Any, config: LanguageConfig, source: bytes) -> str | None:
    """The docstring of a definition, if it has one.

    Recognised as: first statement of the body is a bare string expression. That is
    the shape every Python/JS convention compiles to, and it avoids mistaking a
    string passed to a call for documentation.
    """
    body = _named_child(func_or_class, config.function_body_field)
    if body is None:
        return None
    for child in getattr(body, "children", []):
        if not getattr(child, "is_named", False):
            continue
        # The first *named* statement decides: either it is a string literal
        # (docstring) or this definition has none.
        if child.type == "expression_statement":
            for grand in child.children:
                if grand.type in ("string", "string_literal", "template_string"):
                    return _strip_string_delimiters(_text(grand, source)) or None
            return None
        if child.type in ("string", "string_literal", "template_string"):
            return _strip_string_delimiters(_text(child, source)) or None
        return None
    return None


def _marker_rationale(raw: str) -> str | None:
    """Body of a comment that carries an explicit rationale marker."""
    text = raw.strip()
    for prefix in ("#", "//", "/*", "*", "--"):
        if text.startswith(prefix):
            text = text[len(prefix) :].strip()
    text = text.rstrip("*/").strip()
    upper = text.upper()
    for marker in RATIONALE_MARKERS:
        if upper.startswith(marker.upper()):
            return text[len(marker) :].strip() or None
    return None


def _callee_facts(call_node: Any, config: LanguageConfig, source: bytes) -> dict[str, Any] | None:
    """Describe what a call node targets, without deciding what it resolves to.

    Four shapes matter:

    - ``helper()``        -> bare name, resolvable only through the caller's imports
    - ``Foo.bar()``       -> qualified; resolvable when ``Foo`` is a visible class
    - ``obj.method()``    -> member call on a value; needs a type, so it is recorded
      with the receiver name and left for the type-aware resolvers
    - ``<Widget/>``       -> JSX component usage, which is a call for graph purposes
    """
    if call_node.type in _JSX_CALL_TYPES:
        name_node = _named_child(call_node, "name")
        if name_node is None or name_node.type != "identifier":
            return None
        name = _text(name_node, source)
        # Lowercase tags are intrinsic HTML (``div``, ``span``), not components.
        if not name or not name[0].isupper():
            return None
        return {
            "callee": name,
            "receiver": "",
            "receiver_type": None,
            "is_member_call": False,
            "line": _line(call_node),
            **_span(name_node),
        }

    func_node = _named_child(call_node, config.call_function_field)
    if func_node is None:
        # ``new Foo()`` keeps the constructor in its own field rather than in
        # ``function``; Java names the invocation target in ``name``.
        func_node = _named_child(call_node, "constructor") or _named_child(call_node, "name")
    if func_node is None:
        return None

    if func_node.type in config.accessor_types:
        obj = _named_child(func_node, config.accessor_object_field)
        attr = _named_child(func_node, config.accessor_attr_field)
        if attr is None:
            return None
        attr_name = _text(attr, source)
        receiver = _text(obj, source) if obj is not None else ""
        receiver_type = None
        if receiver in ("self", "this", "cls"):
            # ``self``/``this``/``cls`` name the enclosing class without spelling
            # it out, so the resolver uses the caller's exact class node.
            receiver_type = "<self>"
        elif receiver and receiver[0].isupper():
            # ``Foo.bar()``: the receiver is capitalised, so treat it as a class
            # reference. This is the one member-call shape resolvable without type
            # inference, and graphify resolves it the same way.
            receiver_type = receiver
        return {
            "callee": attr_name,
            "receiver": receiver,
            "receiver_type": receiver_type,
            "is_member_call": True,
            "line": _line(call_node),
            **_span(attr),
        }

    name = _text(func_node, source)
    if not re.fullmatch(r"[A-Za-z_]\w*", name):
        return None
    return {
        "callee": name,
        "receiver": "",
        "receiver_type": None,
        "is_member_call": False,
        "line": _line(call_node),
        **_span(func_node),
    }


def _function_name(node: Any, config: LanguageConfig, source: bytes) -> str | None:
    """The declared name of a function-like node, or the name its binding gives it.

    Arrow functions and ``function () {}`` expressions are anonymous in the grammar.
    Their only useful name is the one they are assigned to, so the immediate parent
    (``variable_declarator``, a class field, or ``=``) is consulted. One wrapper
    level is allowed for ``memo``/``forwardRef``/``observer``, whose component name
    lives on the grandparent declarator. Anything deeper is a guess and returns None.
    """
    name_node = _named_child(node, config.name_field)
    if name_node is None and node.type != "arrow_function":
        # A direct identifier child is a reliable name fallback except on an arrow
        # function, where it is the single unparenthesised parameter.
        name_node = next(
            (c for c in node.children if c.type in ("identifier", "property_identifier", "name")),
            None,
        )
    if name_node is not None:
        return _text(name_node, source) or None

    parent = getattr(node, "parent", None)
    if parent is None:
        return None
    binding = None
    if parent.type in ("variable_declarator", "public_field_definition", "field_definition"):
        binding = _named_child(parent, "name")
    elif parent.type == "assignment_expression":
        left = _named_child(parent, "left")
        if left is not None and left.type in ("identifier", "property_identifier"):
            binding = left
        elif left is not None and left.type == "member_expression":
            binding = _named_child(left, "property")
    elif parent.type in ("call_expression", "arguments"):
        # The grammar puts arguments between the arrow and ``memo(...)``, so the
        # wrapper call is the parent or its parent.
        call = parent if parent.type == "call_expression" else getattr(parent, "parent", None)
        wrapper = _named_child(call, "function") if call is not None else None
        grand = getattr(call, "parent", None) if call is not None else None
        if (
            wrapper is not None
            and wrapper.type == "identifier"
            and _text(wrapper, source) in ("memo", "forwardRef", "observer")
            and grand is not None
            and grand.type == "variable_declarator"
        ):
            binding = _named_child(grand, "name")
    if binding is None:
        return None
    name = _text(binding, source)
    return name if re.fullmatch(r"[A-Za-z_$][\w$]*", name) else None


def extract_file(path: str, source: bytes, config: LanguageConfig) -> Extraction:
    """Parse one file. Never raises: a grammar problem becomes ``error``."""
    result = Extraction(path=path)
    try:
        import importlib

        from tree_sitter import Language, Parser

        module = importlib.import_module(config.ts_module)
        # TypeScript ships two grammars; the tsx one handles JSX and is what our
        # .tsx files need. Everything else exposes ``language()``.
        if config.name == "typescript" and path.lower().endswith(".tsx"):
            capsule = module.language_tsx()
        elif config.name == "typescript":
            capsule = module.language_typescript()
        else:
            capsule = module.language()
        parser = Parser(Language(capsule))
        if source and not source.endswith(b"\n"):
            # Several grammars report a spurious error on a file whose last line
            # has no terminator (C headers especially). Appending one is free.
            source = source + b"\n"
        tree = parser.parse(source)
    except ImportError:
        result.error = f"{config.ts_module} not installed"
        return result
    except Exception as exc:  # pragma: no cover - grammar/driver failure
        result.error = f"{type(exc).__name__}: {exc}"
        return result

    nodes = result.nodes
    edges = result.edges
    seen: set[str] = set()

    def add_node(node_id: str, **attrs: Any) -> None:
        if node_id in seen:
            return
        seen.add(node_id)
        nodes.append({"id": node_id, **attrs})

    def add_edge(source_id: str, target_id: str, relation: str, confidence: str, line: int | None = None) -> None:
        if source_id == target_id:
            return
        edges.append(
            {
                "source": source_id,
                "target": target_id,
                "relation": relation,
                "confidence": confidence,
                "line": line,
            }
        )

    fnode = file_node_id(path)
    total_lines = source.count(b"\n") + 1 if source else 1
    add_node(
        fnode,
        label=path.rsplit("/", 1)[-1],
        kind=KIND_FILE,
        path=path,
        start_line=1,
        start_col=1,
        end_line=total_lines,
        end_col=1,
        language=config.name,
        parent=None,
    )

    # ---- docstrings and marker comments, collected as we go ------------------
    def add_rationale(body: str, owner_id: str, line: int, origin: str) -> None:
        text = " ".join(body.split())
        if not text:
            return
        text = text[:MAX_RATIONALE_CHARS]
        rid = make_node_id(path, KIND_RATIONALE, text[:40], line)
        add_node(
            rid,
            label=text,
            kind=KIND_RATIONALE,
            path=path,
            start_line=line,
            end_line=line,
            language=config.name,
            parent=owner_id,
            origin=origin,
        )
        add_edge(rid, owner_id, REL_RATIONALE_FOR, CONF_EXTRACTED, line)

    # ---- recursive scope walk ------------------------------------------------
    def walk(node: Any, owner_id: str, owner_kind: str, class_name: str | None, class_id: str | None) -> None:
        for child in getattr(node, "children", []):
            if not getattr(child, "is_named", False):
                continue
            ctype = child.type

            if ctype in config.import_types:
                if config.import_parser:
                    for fact in config.import_parser(child, source):
                        result.raw_imports.append({**fact, "source_id": fnode})
                # ``export_statement`` doubles as the wrapper around exported
                # declarations. Parsing its re-export form must not swallow the
                # declaration inside it: ``export function Icon`` and
                # ``export const X = () => …`` are how every React module exposes
                # its components. Import facts are already recorded above.
                if ctype == "export_statement":
                    walk(child, owner_id, owner_kind, class_name, class_id)
                continue

            if ctype in config.class_types:
                name_node = _named_child(child, config.name_field) or next(
                    (c for c in child.children if c.type in ("identifier", "type_identifier", "name")),
                    None,
                )
                name = _text(name_node, source) if name_node is not None else "<anonymous>"
                nid = make_node_id(path, KIND_CLASS, name, _line(child))
                add_node(
                    nid,
                    label=name,
                    kind=KIND_CLASS,
                    path=path,
                    start_line=_line(child),
                    start_col=_start_col(child),
                    end_line=_end_line(child),
                    end_col=_end_col(child),
                    signature=_signature(child, source),
                    language=config.name,
                    parent=owner_id,
                )
                add_edge(owner_id, nid, REL_CONTAINS, CONF_EXTRACTED, _line(child))

                doc = _first_docstring(child, config, source)
                if doc:
                    add_rationale(doc, nid, _line(child), "docstring")

                # Base classes are raw facts: ``Base`` may be defined elsewhere.
                bases_node = _named_child(child, config.superclass_field)
                if bases_node is not None:
                    for base in getattr(bases_node, "children", []):
                        if not getattr(base, "is_named", False):
                            continue
                        if base.type in config.superclass_name_types:
                            result.raw_inherits.append(
                                {
                                    "child_id": nid,
                                    "child_name": name,
                                    "base": _text(base, source),
                                    "line": _line(base),
                                    "column": _start_col(base),
                                    "end_line": _end_line(base),
                                    "end_column": _end_col(base),
                                    "is_interface": base.type in ("generic_name", "subscript"),
                                }
                            )
                walk(child, nid, KIND_CLASS, name, nid)
                continue

            if ctype in config.function_types:
                name = _function_name(child, config, source)
                if name is None:
                    # An unnamed lambda has no node anyone could find or cite, so
                    # its calls belong to the enclosing scope instead of to a
                    # meaningless ``<anonymous>`` symbol.
                    walk(child, owner_id, owner_kind, class_name, class_id)
                    continue
                nid = make_node_id(path, KIND_FUNCTION, name, _line(child))
                add_node(
                    nid,
                    label=name,
                    kind=KIND_FUNCTION,
                    path=path,
                    start_line=_line(child),
                    start_col=_start_col(child),
                    end_line=_end_line(child),
                    end_col=_end_col(child),
                    signature=_signature(child, source),
                    language=config.name,
                    parent=owner_id,
                )
                # ``method`` says "this class owns this function"; ``contains``
                # says "lexically inside". Keeping them apart is what lets the
                # resolver follow Foo.bar() to a method without walking the file tree.
                relation = REL_METHOD if owner_kind == KIND_CLASS else REL_CONTAINS
                add_edge(owner_id, nid, relation, CONF_EXTRACTED, _line(child))

                doc = _first_docstring(child, config, source)
                if doc:
                    add_rationale(doc, nid, _line(child), "docstring")

                walk(child, nid, KIND_FUNCTION, class_name, class_id)
                continue

            if ctype in config.call_types:
                facts = _callee_facts(child, config, source)
                if facts:
                    result.raw_calls.append(
                        {
                            "caller_id": owner_id,
                            "caller_kind": owner_kind,
                            "caller_class": class_name,
                            "caller_class_id": class_id,
                            **facts,
                        }
                    )
                # A bare identifier in argument position is a *reference* to a
                # callable, not a call: ``map(handler)``, ``Thread(target=run)``,
                # ``submit(process)``. Recorded so the resolver can emit
                # ``indirect_call`` from evidence rather than from a failed lookup.
                args_node = _named_child(child, config.call_arguments_field)
                if args_node is not None:
                    for arg in getattr(args_node, "children", []):
                        if not getattr(arg, "is_named", False):
                            continue
                        target_node = arg
                        # ``target=run``: the value sits in the keyword pair.
                        if arg.type in ("keyword_argument", "pair"):
                            target_node = next(
                                (c for c in arg.children if getattr(c, "is_named", False)),
                                None,
                            )
                            if target_node is not None:
                                # First named child is the key; the value follows.
                                named = [c for c in arg.children if getattr(c, "is_named", False)]
                                if len(named) >= 2:
                                    target_node = named[-1]
                        if target_node is None or not re.fullmatch(
                            r"[A-Za-z_]\w*", _text(target_node, source)
                        ):
                            continue
                        name = _text(target_node, source)
                        result.raw_calls.append(
                            {
                                "caller_id": owner_id,
                                "caller_kind": owner_kind,
                                "caller_class": class_name,
                                "caller_class_id": class_id,
                                "callee": name,
                                "receiver": "",
                                "receiver_type": None,
                                "is_member_call": False,
                                "indirect": True,
                                "line": _line(target_node),
                                **_span(target_node),
                            }
                        )
                # Recurse: a call's arguments can contain further calls and
                # lambdas, and their bodies declare things too.
                walk(child, owner_id, owner_kind, class_name, class_id)
                continue

            if ctype in config.comment_types:
                body = _marker_rationale(_text(child, source))
                if body:
                    add_rationale(body, owner_id, _line(child), "comment")
                continue

            walk(child, owner_id, owner_kind, class_name, class_id)

    walk(tree.root_node, fnode, KIND_FILE, None, None)

    # ---- module-level rationale: a header comment explains the file ----------
    header_lines: list[str] = []
    for raw_line in source.decode("utf-8", errors="replace").splitlines()[:12]:
        stripped = raw_line.strip()
        if not stripped:
            if header_lines:
                break
            continue
        if stripped.startswith("#"):
            body = _marker_rationale(stripped)
            if body:
                header_lines.append(body)
        else:
            break
    if header_lines:
        add_rationale(" ".join(header_lines), fnode, 1, "comment")

    return result
