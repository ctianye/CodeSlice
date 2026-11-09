"""Per-language configuration tables.

Each entry is data, not code: the generic walker in ``engine.py`` reads these to
decide what counts as a class, a function, an import or a call. Adding a language
means adding a ``LanguageConfig`` here plus (optionally) an import parser.

Only languages the project actually indexes are wired up. Listing a grammar that
is not installed costs nothing - ``engine.extract`` catches the ImportError and
returns that file with an ``error`` instead of failing the run.
"""

from __future__ import annotations

import re
from typing import Any

from app.graph.ast import LanguageConfig


# --------------------------------------------------------------------------- #
# Python
# --------------------------------------------------------------------------- #
def _python_imports(node: Any, source: bytes) -> list[dict[str, Any]]:
    """Turn one Python import statement into raw facts.

    ``import a.b`` and ``from a.b import c`` are recorded differently on purpose:
    the first imports a whole module, the second imports one name out of it. The
    graph keeps them apart (``imports`` vs ``imports_from``) because "who depends on
    this module" means something different from "who uses this one symbol".
    """
    text = source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")
    line = node.start_point[0] + 1
    facts: list[dict[str, Any]] = []
    stripped = text.strip()

    if stripped.startswith("from "):
        match = re.match(r"from\s+(\.*)([\w.]*)\s+import\s+(.+)", stripped, re.DOTALL)
        if not match:
            return facts
        dots, module, names_blob = match.group(1), match.group(2), match.group(3)
        module = ("." * len(dots)) + module
        names = []
        for chunk in names_blob.strip("() \n").split(","):
            name = chunk.strip().split(" as ")[0].strip()
            if name and name != "*":
                names.append(name)
        facts.append(
            {
                "kind": "from",
                "module": module.strip(),
                "names": names,
                "line": line,
            }
        )
    else:
        for chunk in stripped[len("import ") :].split(","):
            module = chunk.strip().split(" as ")[0].strip()
            if module:
                facts.append({"kind": "plain", "module": module, "names": [], "line": line})
    return facts


PYTHON = LanguageConfig(
    name="python",
    ts_module="tree_sitter_python",
    class_types=frozenset({"class_definition"}),
    function_types=frozenset({"function_definition"}),
    import_types=frozenset({"import_statement", "import_from_statement"}),
    call_types=frozenset({"call"}),
    comment_types=frozenset({"comment"}),
    name_field="name",
    call_function_field="function",
    call_arguments_field="arguments",
    accessor_types=frozenset({"attribute"}),
    accessor_object_field="object",
    accessor_attr_field="attribute",
    function_body_field="body",
    superclass_field="superclasses",
    superclass_name_types=frozenset({"identifier", "attribute"}),
    import_parser=_python_imports,
)


# --------------------------------------------------------------------------- #
# JavaScript / TypeScript
# --------------------------------------------------------------------------- #
_JS_IMPORT_RE = re.compile(
    r"""(?:^|\n)\s*import\s+(?:type\s+)?(?:(?P<default>[A-Za-z_$][\w$]*)\s*,?\s*)?(?:\{(?P<named>[^}]*)\})?\s*(?:\*\s+as\s+(?P<ns>[A-Za-z_$][\w$]*))?\s*from\s*['"](?P<mod>[^'"\n]+)['"]"""
    r"""|(?:^|\n)\s*import\s+['"](?P<bare>[^'"\n]+)['"]"""
    r"""|(?:^|\n)\s*(?:const|let|var)\s+[A-Za-z_$][\w$]*\s*=\s*require\(\s*['"](?P<req>[^'"\n]+)['"]\s*\)"""
    r"""|(?:^|\n)\s*export\s+(?:\*|\{[^}]*\})\s*from\s*['"](?P<reexp>[^'"\n]+)['"]"""
)


def _js_imports(node: Any, source: bytes) -> list[dict[str, Any]]:
    """Import facts from one JS/TS statement.

    Anchored at line starts on purpose. An unanchored ``from 'x'`` pattern also
    matches JSX attributes and string literals inside expressions, which is how a
    previous version ended up with ``aria-label=`` and ``button`` as imported
    modules. Only real statement heads are considered.
    """
    text = source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")
    line = node.start_point[0] + 1
    facts: list[dict[str, Any]] = []
    for match in _JS_IMPORT_RE.finditer(text):
        module = match.group("mod") or match.group("bare") or match.group("req") or match.group("reexp")
        if not module:
            continue
        names: list[str] = []
        if match.group("default"):
            names.append(match.group("default"))
        if match.group("ns"):
            names.append(match.group("ns"))
        if match.group("named"):
            for chunk in match.group("named").split(","):
                name = chunk.strip().split(" as ")[0].strip()
                if name:
                    names.append(name)
        facts.append(
            {
                "kind": "from" if names else "plain",
                "module": module,
                "names": names,
                "line": line,
            }
        )
    return facts


#: Keys shared by the JS and TS configs. A plain dict (not a literal) on purpose:
#: both configs spread it with ``**`` and TS overrides the differing keys, so the
#: shape has to be splattable rather than inlined twice.
_JS_COMMON = dict(  # noqa: C408
    class_types=frozenset({"class_declaration", "class"}),
    function_types=frozenset(
        {
            "function_declaration",
            "generator_function_declaration",
            "method_definition",
            "function_expression",
            "arrow_function",
        }
    ),
    import_types=frozenset({"import_statement", "export_statement"}),
    call_types=frozenset({"call_expression", "new_expression"}),
    name_field="name",
    call_function_field="function",
    call_arguments_field="arguments",
    accessor_types=frozenset({"member_expression"}),
    accessor_object_field="object",
    accessor_attr_field="property",
    function_body_field="body",
    superclass_field=None,
    superclass_name_types=frozenset(),
    import_parser=_js_imports,
)

JAVASCRIPT = LanguageConfig(name="javascript", ts_module="tree_sitter_javascript", **_JS_COMMON)

# TypeScript overrides the shared JS table where its grammar differs: it exposes
# interfaces, abstract classes, enums and type aliases as their own node types, and
# method *signatures* (declarations without bodies) are contracts a subclass must
# implement, so they belong in the graph too.
TYPESCRIPT = LanguageConfig(
    name="typescript",
    ts_module="tree_sitter_typescript",
    **{
        **_JS_COMMON,
        "class_types": frozenset(
            {
                "class_declaration",
                "abstract_class_declaration",
                "interface_declaration",
                "enum_declaration",
                "type_alias_declaration",
            }
        ),
        "function_types": frozenset(
            {
                "function_declaration",
                "generator_function_declaration",
                "method_definition",
                "method_signature",
                "abstract_method_signature",
                "function_expression",
                "arrow_function",
            }
        ),
    },
)


# --------------------------------------------------------------------------- #
# Go / Rust / Java / C / C++ (structural only, no cross-file call resolution)
# --------------------------------------------------------------------------- #
GO = LanguageConfig(
    name="go",
    ts_module="tree_sitter_go",
    class_types=frozenset({"type_declaration", "struct_type", "interface_type"}),
    function_types=frozenset({"function_declaration", "method_declaration"}),
    import_types=frozenset({"import_declaration"}),
    call_types=frozenset({"call_expression"}),
    name_field="name",
    call_function_field="function",
    call_arguments_field="arguments",
    function_body_field="body",
)

RUST = LanguageConfig(
    name="rust",
    ts_module="tree_sitter_rust",
    class_types=frozenset({"struct_item", "enum_item", "trait_item", "impl_item"}),
    function_types=frozenset({"function_item"}),
    import_types=frozenset({"use_declaration"}),
    call_types=frozenset({"call_expression"}),
    name_field="name",
    call_function_field="function",
    call_arguments_field="arguments",
    function_body_field="body",
)

JAVA = LanguageConfig(
    name="java",
    ts_module="tree_sitter_java",
    class_types=frozenset({"class_declaration", "interface_declaration", "enum_declaration"}),
    function_types=frozenset({"method_declaration", "constructor_declaration"}),
    import_types=frozenset({"import_declaration"}),
    call_types=frozenset({"method_invocation"}),
    name_field="name",
    call_function_field="name",
    call_arguments_field="arguments",
    function_body_field="body",
)

C = LanguageConfig(
    name="c",
    ts_module="tree_sitter_c",
    class_types=frozenset({"struct_specifier", "union_specifier"}),
    function_types=frozenset({"function_definition"}),
    import_types=frozenset({"preproc_include"}),
    call_types=frozenset({"call_expression"}),
    name_field="declarator",
    call_function_field="function",
    call_arguments_field="arguments",
    function_body_field="body",
)

CPP = LanguageConfig(
    name="cpp",
    ts_module="tree_sitter_cpp",
    class_types=frozenset({"class_specifier", "struct_specifier", "namespace_definition"}),
    function_types=frozenset({"function_definition"}),
    import_types=frozenset({"preproc_include"}),
    call_types=frozenset({"call_expression"}),
    name_field="declarator",
    call_function_field="function",
    call_arguments_field="arguments",
    function_body_field="body",
)


#: Suffix -> config. Only these get AST extraction; everything else in the index
#: still becomes a file node so the graph is not silently partial.
BY_SUFFIX: dict[str, LanguageConfig] = {
    ".py": PYTHON,
    ".pyi": PYTHON,
    ".js": JAVASCRIPT,
    ".jsx": JAVASCRIPT,
    ".mjs": JAVASCRIPT,
    ".cjs": JAVASCRIPT,
    ".ts": TYPESCRIPT,
    ".tsx": TYPESCRIPT,
    ".mts": TYPESCRIPT,
    ".cts": TYPESCRIPT,
    ".go": GO,
    ".rs": RUST,
    ".java": JAVA,
    ".c": C,
    ".h": C,
    ".cc": CPP,
    ".cpp": CPP,
    ".cxx": CPP,
    ".hpp": CPP,
    ".hh": CPP,
}


def config_for(path: str) -> LanguageConfig | None:
    suffix = ("." + path.rsplit(".", 1)[-1].lower()) if "." in path else ""
    return BY_SUFFIX.get(suffix)


#: Alias used by the tests and by callers that think in terms of "which language
#: handles this file" rather than "look up the suffix".
by_suffix_config = config_for
