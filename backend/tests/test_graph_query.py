"""Pure-function tests for the graph query helpers.

``wants_structure_overview`` decides when a question is about project *shape*
rather than about a symbol, and ``render_communities`` turns the clustering into
the module overview that answers such a question. Both run over plain dicts, so
they are tested without a database; the fallback wiring is covered through
``service.context_for_question`` with a synthetic graph.
"""

from __future__ import annotations

from pathlib import Path

from app.graph import query, service


def _community(cid: int, label: str, size: int, *, pooled: bool = False, hub: str = "", representative: str = ""):
    return {
        "id": cid,
        "label": label,
        "size": size,
        "pooled": pooled,
        "hub": hub,
        "kind": "file",
        "representative": representative,
    }


def _node(nid: str, label: str, path: str, *, kind: str = "function") -> dict:
    return {
        "id": nid,
        "label": label,
        "path": path,
        "kind": kind,
        "start_line": 1,
        "end_line": 4,
        "degree": 0,
        "community": 0,
    }


def test_render_communities_orders_by_size_and_skips_the_pool():
    communities = [
        _community(0, "small.py", 3),
        _community(1, "big.py +2", 250, hub="src/big.py::file::__file__::1", representative="run"),
        _community(2, "未分类", 40, pooled=True),
    ]

    text = query.render_communities(communities, token_budget=1200)

    assert text.startswith("Module overview: 2 communities")
    assert text.index("big.py +2") < text.index("small.py")
    # The pool holds isolated nodes, not a subsystem: it must not look like one.
    assert "未分类" not in text
    assert "src/big.py" in text and "run" in text


def test_render_communities_caps_the_number_of_rows():
    communities = [_community(i, f"mod{i}.py", 100 - i) for i in range(40)]

    text = query.render_communities(communities, token_budget=1200)

    rows = [line for line in text.splitlines() if line.startswith("  - ")]
    assert len(rows) == query.OVERVIEW_MAX_ROWS
    assert "40 communities" in text


def test_render_communities_notes_the_budget_cut():
    # Long labels exhaust the character budget before the row cap is reached.
    communities = [
        _community(i, "deep/" + "x" * 220 + f".py +{i}", 100 - i, hub=("y" * 240) + "::file::__file__::1")
        for i in range(20)
    ]

    text = query.render_communities(communities, token_budget=1200)

    assert "more communities omitted" in text
    assert len(text) <= query.OVERVIEW_MAX_TOKENS * query.CHARS_PER_TOKEN + 200


def test_render_communities_is_empty_when_nothing_is_worth_showing():
    assert query.render_communities([]) == ""
    assert query.render_communities([_community(0, "未分类", 9, pooled=True)]) == ""


def test_wants_structure_overview_reads_shape_questions_only():
    assert query.wants_structure_overview("整个项目的模块是怎么划分的")
    assert query.wants_structure_overview("讲一下这个项目的整体框架")
    assert query.wants_structure_overview("give me the architecture overview")
    # A symbol that merely contains a structural word is not a shape question.
    assert not query.wants_structure_overview("app.cpp 里 SubModule 是怎么注册的")
    assert not query.wants_structure_overview("")


def test_needs_overview_rules():
    assert service._needs_overview("随便问问", [], {})
    # Shape questions get the overview even when seeds were found.
    assert service._needs_overview("整体框架", ["a"], {"a": 99.0})
    # A real symbol question with a strong seed does not.
    assert not service._needs_overview("SubModule 怎么注册的", ["a"], {"a": 50.0})
    # A weak seed is a shared character, not a named symbol.
    assert service._needs_overview("SubModule 怎么注册的", ["a"], {"a": 0.1})


def test_context_for_question_falls_back_to_the_module_overview(monkeypatch, tmp_path: Path):
    graph = {
        "nodes": [_node("core.py::f::1", "helper", "core.py")],
        "edges": [],
        "communities": [_community(0, "core.py", 12, hub="core.py::file::__file__::1")],
    }
    monkeypatch.setattr(service, "load", lambda *args, **kwargs: graph)

    text = service.context_for_question(1, tmp_path, "整个项目的模块是怎么划分的", token_budget=1200)

    assert "Module overview" in text
    assert query.NO_MATCH_MESSAGE not in text
    assert len(text) <= 1200 * query.CHARS_PER_TOKEN


def test_context_for_question_keeps_the_message_when_no_communities_exist(monkeypatch, tmp_path: Path):
    graph = {"nodes": [_node("core.py::f::1", "helper", "core.py")], "edges": [], "communities": []}
    monkeypatch.setattr(service, "load", lambda *args, **kwargs: graph)

    text = service.context_for_question(1, tmp_path, "zzz qqq", token_budget=1200)

    assert text == query.NO_MATCH_MESSAGE


def test_context_for_question_stays_subgraph_for_symbol_questions(monkeypatch, tmp_path: Path):
    graph = {
        "nodes": [_node("app.cpp::SubModule::1", "SubModule", "app.cpp", kind="class")],
        "edges": [],
        "communities": [_community(0, "app.cpp", 12, hub="app.cpp::file::__file__::1")],
    }
    monkeypatch.setattr(service, "load", lambda *args, **kwargs: graph)

    text = service.context_for_question(1, tmp_path, "app.cpp 里 SubModule 是怎么注册的", token_budget=1200)

    assert "Subgraph" in text
    assert "Module overview" not in text
