"""Tests for editor navigation: go-to-definition, references and hover."""

from __future__ import annotations

import pytest


@pytest.fixture()
def nav_project(tmp_path, conn_reset):
    root = tmp_path / "nav"
    root.mkdir()
    (root / "a.py").write_text(
        '"""Definitions."""\n'
        "\n"
        "\n"
        "def helper():\n"
        '    """Help the caller."""\n'
        "    return 1\n"
        "\n"
        "\n"
        "class Foo:\n"
        "    def method(self):\n"
        "        return 2\n"
        "\n"
        "    def run(self):\n"
        "        return self.method()\n",
        encoding="utf-8",
    )
    (root / "b.py").write_text(
        "from a import helper\n"
        "from a import method as renamed\n"
        "\n"
        "def caller():\n"
        "    helper()\n"
        "    renamed()\n",
        encoding="utf-8",
    )
    from app.storage import repo

    return repo.create_project("nav", str(root))


def _definition(client, project, path, line, column):
    response = client.get(
        f"/api/projects/{project['id']}/definition",
        params={"path": path, "line": line, "column": column},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_cross_file_call_goes_to_the_definition(client, nav_project):
    body = _definition(client, nav_project, "b.py", 5, 5)
    assert body["reason"] is None
    assert len(body["targets"]) == 1
    target = body["targets"][0]
    assert target["path"] == "a.py"
    assert target["name"] == "helper"
    assert target["kind"] == "function"
    assert target["line"] == 4
    assert target["confidence"] == "EXTRACTED"


def test_import_alias_goes_to_the_original_symbol(client, nav_project):
    body = _definition(client, nav_project, "b.py", 6, 5)
    assert [target["name"] for target in body["targets"]] == ["method"]


def test_self_method_resolves_within_the_class(client, nav_project):
    body = _definition(client, nav_project, "a.py", 14, 22)
    assert [target["name"] for target in body["targets"]] == ["method"]


def test_clicking_an_imported_name_goes_to_its_definition(client, nav_project):
    body = _definition(client, nav_project, "b.py", 1, 15)
    assert [target["name"] for target in body["targets"]] == ["helper"]
    assert body["targets"][0]["path"] == "a.py"


def test_ambiguous_name_returns_candidates_instead_of_guessing(client, tmp_path, conn_reset):
    root = tmp_path / "ambiguous"
    root.mkdir()
    (root / "a.py").write_text("def helper():\n    pass\n", encoding="utf-8")
    (root / "b.py").write_text("def helper():\n    pass\n", encoding="utf-8")
    (root / "c.py").write_text("def caller():\n    helper()\n", encoding="utf-8")
    from app.storage import repo

    project = repo.create_project("ambiguous", str(root))
    body = _definition(client, project, "c.py", 2, 5)
    assert body["targets"] == []
    assert body["reason"] == "ambiguous"
    assert {item["path"] for item in body["candidates"]} == {"a.py", "b.py"}

    hover = client.get(
        f"/api/projects/{project['id']}/hover",
        params={"path": "c.py", "line": 2, "column": 5},
    ).json()
    assert hover["reason"] == "ambiguous"
    assert "多个同名定义" in hover["contents"]
    assert "a.py:1" in hover["contents"]
    assert "b.py:1" in hover["contents"]


def test_f12_on_a_definition_returns_itself(client, nav_project):
    body = _definition(client, nav_project, "a.py", 4, 5)
    assert [target["name"] for target in body["targets"]] == ["helper"]
    assert body["targets"][0]["line"] == 4


def test_references_list_every_occurrence(client, nav_project):
    response = client.get(
        f"/api/projects/{nav_project['id']}/references",
        params={"path": "a.py", "line": 4, "column": 5},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["target"]["name"] == "helper"
    locations = {(item["path"], item["line"]) for item in body["locations"]}
    assert ("b.py", 1) in locations
    assert ("b.py", 5) in locations


def test_hover_includes_signature_docstring_and_location(client, nav_project):
    response = client.get(
        f"/api/projects/{nav_project['id']}/hover",
        params={"path": "b.py", "line": 5, "column": 5},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["contents"]
    assert "def helper()" in body["contents"]
    assert "Help the caller." in body["contents"]
    assert "a.py:4" in body["contents"]


def test_external_import_does_not_offer_a_same_named_project_symbol(client, tmp_path, conn_reset):
    root = tmp_path / "external"
    root.mkdir()
    (root / "path.py").write_text("def path():\n    pass\n", encoding="utf-8")
    (root / "consumer.py").write_text("from os import path\n", encoding="utf-8")
    from app.storage import repo

    project = repo.create_project("external", str(root))
    body = _definition(client, project, "consumer.py", 1, 15)
    assert body["targets"] == []
    assert body["candidates"] == []
    assert body["reason"] == "external"

    hover = client.get(
        f"/api/projects/{project['id']}/hover",
        params={"path": "consumer.py", "line": 1, "column": 15},
    ).json()
    assert "外部符号" in hover["contents"]
    assert "path" in hover["contents"]


def test_path_traversal_is_rejected(client, nav_project):
    response = client.get(
        f"/api/projects/{nav_project['id']}/definition",
        params={"path": "../secret.py", "line": 1, "column": 1},
    )
    assert response.status_code == 400


def test_unknown_position_returns_a_reason(client, nav_project):
    body = _definition(client, nav_project, "b.py", 3, 1)
    assert body["targets"] == []
    assert body["reason"] in {"no_definition", "unresolved"}


def test_symbol_search_finds_project_definitions(client, nav_project):
    response = client.get(
        f"/api/projects/{nav_project['id']}/symbols",
        params={"q": "help"},
    )
    assert response.status_code == 200
    names = [item["name"] for item in response.json()["symbols"]]
    assert "helper" in names
