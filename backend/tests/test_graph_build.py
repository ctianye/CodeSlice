"""Tests for the AST extraction pipeline.

The cases here each encode a decision that would otherwise produce a plausible but
wrong graph, which is worse than an obviously broken one:

* a definition must not be reported as a call to itself
* an ambiguous name must produce no edge rather than the wrong one
* a docstring must become a ``rationale`` node, not be dropped
* a package import must find the package, not a same-named file elsewhere
"""

from __future__ import annotations

from app.graph.ast.engine import extract_file
from app.graph.ast.languages import PYTHON, by_suffix_config
from app.graph.ast.resolve import SymbolTable, resolve


def _extract(path: str, code: str):
    return extract_file(path, code.encode("utf-8"), PYTHON)


def _labels(graph: dict) -> dict[str, dict]:
    return {node["label"]: node for node in graph["nodes"]}


def _edges(graph: dict, relation: str) -> set[tuple[str, str]]:
    by_id = {node["id"]: node for node in graph["nodes"]}
    out = set()
    for edge in graph["edges"]:
        if edge["relation"] != relation:
            continue
        src = by_id.get(edge["source"], {}).get("label", "?")
        dst = by_id.get(edge["target"], {}).get("label", "?")
        out.add((src, dst))
    return out


def _build(*files: tuple[str, str]) -> dict:

    extractions = [_extract(path, code) for path, code in files]
    return resolve(extractions)


# --------------------------------------------------------------------------- #
# structure
# --------------------------------------------------------------------------- #
def test_a_file_becomes_a_node_with_symbols_inside_it():
    graph = _build(("a.py", "class Foo:\n    def run(self):\n        pass\n"))
    labels = _labels(graph)
    assert "a.py" in labels and labels["a.py"]["kind"] == "file"
    assert labels["Foo"]["kind"] == "class"
    assert labels["run"]["kind"] == "function"
    assert ("a.py", "Foo") in _edges(graph, "contains")
    # A method hangs off its class with `method`, not `contains`: the distinction
    # lets the resolver follow Foo.bar() without walking the file tree.
    assert ("Foo", "run") in _edges(graph, "method")


def test_line_ranges_are_exact():
    graph = _build(("a.py", "def first():\n    pass\n\n\ndef second():\n    pass\n"))
    labels = _labels(graph)
    assert labels["first"]["start_line"] == 1
    assert labels["second"]["start_line"] == 5


# --------------------------------------------------------------------------- #
# calls
# --------------------------------------------------------------------------- #
def test_a_definition_is_not_a_call_to_itself():
    """The single most damaging false positive in a call graph."""
    graph = _build(("a.py", "def run():\n    return 1\n"))
    assert ("run", "run") not in _edges(graph, "calls")


def test_a_same_file_call_resolves():
    graph = _build(("a.py", "def helper():\n    pass\ndef caller():\n    helper()\n"))
    assert ("caller", "helper") in _edges(graph, "calls")


def test_a_cross_file_call_resolves_through_the_unique_name():
    graph = _build(
        ("a.py", "def helper():\n    pass\n"),
        ("b.py", "def caller():\n    helper()\n"),
    )
    assert ("caller", "helper") in _edges(graph, "calls")


def test_an_ambiguous_name_produces_no_edge():
    """Two `helper` definitions make a bare call unresolvable.

    Guessing one would cite a caller that does not exist; dropping the edge keeps
    the graph honest.
    """
    graph = _build(
        ("a.py", "def helper():\n    pass\n"),
        ("b.py", "def helper():\n    pass\n"),
        ("c.py", "def caller():\n    helper()\n"),
    )
    assert ("caller", "helper") not in _edges(graph, "calls")


def test_a_class_qualified_call_resolves_to_the_method():
    """`Foo.bar()` names the class, so no inference is needed."""
    graph = _build(
        (
            "a.py",
            "class Foo:\n    def bar(self):\n        pass\n\n"
            "def caller():\n    Foo.bar()\n",
        )
    )
    assert ("caller", "bar") in _edges(graph, "calls")


def test_a_self_call_resolves_within_the_class():
    graph = _build(
        (
            "a.py",
            "class Foo:\n"
            "    def helper(self):\n        pass\n"
            "    def run(self):\n        self.helper()\n",
        )
    )
    assert ("run", "helper") in _edges(graph, "calls")


def test_a_builtin_is_never_attributed_to_a_project_symbol():
    graph = _build(
        ("a.py", "def print(x):\n    pass\n"),
        ("b.py", "def caller():\n    print('hi')\n"),
    )
    assert ("caller", "print") not in _edges(graph, "calls")


def test_resolved_calls_are_extracted_not_inferred():
    graph = _build(("a.py", "def helper():\n    pass\ndef caller():\n    helper()\n"))
    calls = [e for e in graph["edges"] if e["relation"] == "calls"]
    assert calls
    assert all(e["confidence"] == "EXTRACTED" for e in calls)


# --------------------------------------------------------------------------- #
# rationale
# --------------------------------------------------------------------------- #
def test_a_docstring_becomes_a_rationale_node():
    graph = _build(
        (
            "a.py",
            'def retitle(conversation_id, answer):\n'
            '    """Replace an auto-generated title with one that names the topic."""\n'
            "    return None\n",
        )
    )
    rationale = [n for n in graph["nodes"] if n["kind"] == "rationale"]
    assert len(rationale) == 1
    assert "auto-generated title" in rationale[0]["label"]
    assert any(e["relation"] == "rationale_for" for e in graph["edges"])


def test_a_marked_comment_becomes_a_rationale_node():
    graph = _build(
        (
            "a.py",
            "# WHY: the planner needs the full list before it can prune\n"
            "def plan(items):\n"
            "    return items\n",
        )
    )
    rationale = [n for n in graph["nodes"] if n["kind"] == "rationale"]
    assert rationale
    assert any("planner needs the full list" in n["label"] for n in rationale)


def test_a_plain_comment_is_not_a_rationale():
    graph = _build(("a.py", "# just a separator\ndef f():\n    pass\n"))
    assert not [n for n in graph["nodes"] if n["kind"] == "rationale"]


def test_rationale_points_at_the_code_it_explains():
    graph = _build(
        ("a.py", 'def f():\n    """Explains f."""\n    return 1\n')
    )
    by_id = {n["id"]: n for n in graph["nodes"]}
    pairs = [(by_id[e["source"]], by_id[e["target"]]) for e in graph["edges"] if e["relation"] == "rationale_for"]
    assert pairs
    src, dst = pairs[0]
    assert src["kind"] == "rationale"
    assert dst["label"] == "f"


# --------------------------------------------------------------------------- #
# imports
# --------------------------------------------------------------------------- #
def test_an_internal_import_links_two_files():
    graph = _build(
        ("app/a.py", "from app.helpers import thing\n"),
        ("app/helpers.py", "def thing():\n    pass\n"),
    )
    assert ("a.py", "helpers.py") in _edges(graph, "imports_from")


def test_an_external_import_becomes_an_external_node():
    graph = _build(("a.py", "import json\n"))
    external = [n for n in graph["nodes"] if n["kind"] == "external"]
    assert [n["label"] for n in external] == ["json"]
    # External dependencies use `uses`, so the `imports` count stays about the
    # project's own modules.
    assert ("a.py", "json") in _edges(graph, "uses")


def test_a_package_import_finds_the_package_init():
    """`from app.agent import graph` must find the package, not a loose file."""
    extractions = [
        _extract("app/a.py", "from app.agent import graph\n"),
        _extract("app/agent/__init__.py", ""),
        _extract("app/agent/graph.py", "def build():\n    pass\n"),
    ]
    table = SymbolTable(extractions)
    assert table.resolve_module("app.agent") == ["app/agent/__init__.py"]
    assert table.resolve_module("app.agent.graph") == ["app/agent/graph.py"]


def test_a_package_import_links_the_importing_file_to_the_init():
    graph = _build(
        ("app/a.py", "from app.agent import graph\n"),
        ("app/agent/__init__.py", ""),
        ("app/agent/graph.py", "def build():\n    pass\n"),
    )
    assert ("a.py", "__init__.py") in _edges(graph, "imports_from")


def test_a_relative_js_import_is_parsed():
    from app.graph.ast.engine import extract_file as run
    from app.graph.ast.languages import JAVASCRIPT

    result = run("src/a.js", b"import { helper } from './lib/helper'\n", JAVASCRIPT)
    assert result.raw_imports
    assert result.raw_imports[0]["module"] == "./lib/helper"
    assert result.raw_imports[0]["names"] == ["helper"]


def test_jsx_attributes_are_not_mistaken_for_imports():
    """An unanchored `from '...'` pattern matches JSX; nothing else may."""
    from app.graph.ast.engine import extract_file as run
    from app.graph.ast.languages import JAVASCRIPT

    code = b'const el = <button aria-label="x" title="y">hi</button>\n'
    result = run("src/b.js", code, JAVASCRIPT)
    assert result.raw_imports == []


# --------------------------------------------------------------------------- #
# inheritance
# --------------------------------------------------------------------------- #
def test_inheritance_resolves_to_the_base_class():
    graph = _build(
        ("a.py", "class Base:\n    pass\n\nclass Child(Base):\n    pass\n")
    )
    assert ("Child", "Base") in _edges(graph, "inherits")


def test_inheritance_from_a_builtin_links_to_an_external_node():
    """A base class outside the corpus is still a fact about the class.

    ``class Err(ValueError)`` tells a reader this is an exception. Recording the
    edge against an ``external`` node keeps that while staying honest that the
    definition is not in this project - which is how graphify treats it too.
    """
    graph = _build(("a.py", "class Err(ValueError):\n    pass\n"))
    assert ("Err", "ValueError") in _edges(graph, "inherits")
    external = [n for n in graph["nodes"] if n["kind"] == "external"]
    assert [n["label"] for n in external] == ["ValueError"]


# --------------------------------------------------------------------------- #
# robustness
# --------------------------------------------------------------------------- #
def test_an_unparsable_language_is_reported_not_raised():
    from app.graph.ast import LanguageConfig
    from app.graph.ast.engine import extract_file as run

    config = LanguageConfig(name="nope", ts_module="tree_sitter_does_not_exist")
    result = run("x.nope", b"content", config)
    assert result.error
    assert result.nodes == []


def test_an_empty_file_still_yields_its_node():
    graph = _build(("empty.py", ""))
    labels = _labels(graph)
    assert "empty.py" in labels


def test_stats_count_rationale_and_relations():
    graph = _build(("a.py", 'def f():\n    """Why."""\n    return 1\n'))
    stats = graph["stats"]
    assert stats["rationale"] == 1
    assert stats["files"] == 1
    assert stats["by_relation"].get("rationale_for") == 1


def test_by_suffix_config_knows_python_and_rejects_unknown():
    assert by_suffix_config("x.py") is PYTHON
    assert by_suffix_config("x.bin") is None
