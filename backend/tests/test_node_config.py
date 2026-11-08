"""Node configuration: defaults, merging, validation and run stats.

These cover the contract the workflow panel relies on: a partial patch merges over
what is stored, a bad patch is a 400 rather than a silently ignored key, and stats
recording can never blow up a turn.
"""

from __future__ import annotations

import pytest

from app.agent import node_config
from app.core.errors import InvalidInput, NotFound
from app.storage import repo


def _clear() -> None:
    repo.set_setting(node_config.SETTINGS_KEY, {})


@pytest.fixture(autouse=True)
def clean_settings(conn_reset):
    _clear()
    # Stats live in a module-level dict; other tests' turns would leak into here.
    with node_config._LOCK:
        node_config._STATS.clear()
    yield
    _clear()
    with node_config._LOCK:
        node_config._STATS.clear()


# --------------------------------------------------------------------------- #
# defaults and persistence
# --------------------------------------------------------------------------- #
def test_load_returns_defaults_when_nothing_is_saved():
    config = node_config.load()
    assert list(config) == ["prepare", "agent", "run_one", "finalize"]
    assert config["prepare"] == {"retrieval_top_k": 6, "include_git_branch": True}
    assert config["agent"]["max_tool_rounds"] == 10
    assert config["agent"]["temperature"] == 0.2
    assert config["run_one"]["auto_approve_readonly_tools"] is True
    assert config["finalize"] == {"auto_retitle": True}


def test_unknown_node_is_not_found():
    with pytest.raises(NotFound):
        node_config.get_node("nope")
    with pytest.raises(NotFound):
        node_config.validate("nope", {"x": 1})
    with pytest.raises(NotFound):
        node_config.save_node("nope", {})


# --------------------------------------------------------------------------- #
# saving
# --------------------------------------------------------------------------- #
def test_save_then_load_round_trips():
    saved = node_config.save_node("agent", {"temperature": 0.7, "max_tokens": 2048})
    assert saved["config"]["temperature"] == 0.7
    assert saved["config"]["max_tokens"] == 2048
    # Untouched keys keep their defaults.
    assert saved["config"]["max_tool_rounds"] == 10
    assert node_config.load()["agent"]["temperature"] == 0.7


def test_save_merges_partial_patches():
    node_config.save_node("prepare", {"retrieval_top_k": 3})
    node_config.save_node("prepare", {"include_git_branch": False})
    config = node_config.load()["prepare"]
    assert config == {"retrieval_top_k": 3, "include_git_branch": False}


def test_legacy_row_missing_keys_falls_back_to_defaults():
    # A row written by an older version only knew about one key.
    repo.set_setting(node_config.SETTINGS_KEY, {"agent": {"max_tokens": 1024}})
    config = node_config.load()["agent"]
    assert config["max_tokens"] == 1024
    assert config["temperature"] == 0.2
    assert config["extra_prompt"] == ""
    assert config["max_tool_rounds"] == 10


def test_saving_one_node_leaves_the_others_alone():
    node_config.save_node("finalize", {"auto_retitle": False})
    config = node_config.load()
    assert config["finalize"] == {"auto_retitle": False}
    assert config["prepare"] == {"retrieval_top_k": 6, "include_git_branch": True}


# --------------------------------------------------------------------------- #
# validation
# --------------------------------------------------------------------------- #
def test_unknown_key_is_rejected():
    with pytest.raises(InvalidInput, match="unknown config key"):
        node_config.validate("prepare", {"nope": 1})


def test_int_out_of_range_is_rejected():
    with pytest.raises(InvalidInput, match=">= 1"):
        node_config.validate("prepare", {"retrieval_top_k": 0})
    with pytest.raises(InvalidInput, match="<= 20"):
        node_config.validate("prepare", {"retrieval_top_k": 21})


def test_float_out_of_range_is_rejected():
    with pytest.raises(InvalidInput, match="<= 2"):
        node_config.validate("agent", {"temperature": 2.5})
    with pytest.raises(InvalidInput, match=">= 0"):
        node_config.validate("agent", {"temperature": -0.1})


def test_wrong_types_are_rejected():
    with pytest.raises(InvalidInput, match="integer"):
        node_config.validate("agent", {"max_tokens": "2048"})
    with pytest.raises(InvalidInput, match="boolean"):
        node_config.validate("prepare", {"include_git_branch": "yes"})
    with pytest.raises(InvalidInput, match="number"):
        node_config.validate("agent", {"temperature": "hot"})
    with pytest.raises(InvalidInput, match="string"):
        node_config.validate("agent", {"model": 3})
    with pytest.raises(InvalidInput, match="text"):
        node_config.validate("agent", {"extra_prompt": 1})


def test_bool_is_not_accepted_as_a_number():
    with pytest.raises(InvalidInput, match="integer"):
        node_config.validate("agent", {"max_tokens": True})


def test_config_must_be_an_object():
    with pytest.raises(InvalidInput, match="object"):
        node_config.validate("prepare", ["nope"])


def test_int_accepts_a_float_that_is_whole_only_via_float_field():
    # The float field stores a float; the int field stays strict.
    assert node_config.validate("agent", {"temperature": 1})["temperature"] == 1.0
    with pytest.raises(InvalidInput):
        node_config.validate("agent", {"max_tokens": 1024.0})


def test_a_rejected_patch_persists_nothing():
    with pytest.raises(InvalidInput):
        node_config.save_node("prepare", {"retrieval_top_k": 99})
    assert node_config.load()["prepare"]["retrieval_top_k"] == 6


# --------------------------------------------------------------------------- #
# node payload
# --------------------------------------------------------------------------- #
def test_node_payload_matches_the_http_contract():
    node = node_config.get_node("agent")
    assert node["id"] == "agent"
    assert node["kind"] == "llm"
    assert node["editable"]["max_tokens"] == {"type": "int", "min": 1024, "max": 262144}
    assert node["defaults"]["max_tokens"] == 32768
    assert set(node["config"]) == set(node["defaults"])
    assert isinstance(node["stats"], dict)


def test_payload_lists_nodes_in_graph_order():
    nodes = node_config.nodes_payload()
    assert [n["id"] for n in nodes] == ["prepare", "agent", "run_one", "finalize"]
    assert [n["kind"] for n in nodes] == ["input", "llm", "tool", "output"]


def test_editable_payload_is_a_copy():
    node_config.get_node("agent")["editable"]["max_tokens"]["max"] = 1
    assert node_config.NODES["agent"]["editable"]["max_tokens"]["max"] == 262144


# --------------------------------------------------------------------------- #
# run stats
# --------------------------------------------------------------------------- #
def test_record_and_read_stats():
    node_config.record_run("agent", last_tokens=1234, last_duration_ms=56)
    stats = node_config.stats_for("agent")
    assert stats["last_tokens"] == 1234
    assert stats["last_duration_ms"] == 56
    assert "at" in stats
    # Stats ride along in the node payload.
    assert node_config.get_node("agent")["stats"]["last_tokens"] == 1234


def test_record_run_drops_empty_values():
    node_config.record_run("agent", last_tokens=None, last_duration_ms=5)
    stats = node_config.stats_for("agent")
    assert "last_tokens" not in stats
    assert stats["last_duration_ms"] == 5


def test_record_run_never_raises(monkeypatch):
    class Boom:
        def isoformat(self, *args, **kwargs):
            raise RuntimeError("boom")

    class BrokenDatetime:
        @staticmethod
        def now(*args, **kwargs):
            return Boom()

    monkeypatch.setattr(node_config, "datetime", BrokenDatetime)
    node_config.record_run("agent", last_tokens=1)  # must not raise
    assert isinstance(node_config.stats_for("agent"), dict)


def test_stats_for_an_unseen_node_is_empty():
    assert node_config.stats_for("finalize") == {}


# --------------------------------------------------------------------------- #
# graph.py actually reads the stored config
# --------------------------------------------------------------------------- #
def test_graph_reads_the_extra_prompt():
    from app.agent import graph as agent_graph

    assert agent_graph._extra_prompt() is None  # empty by default
    node_config.save_node("agent", {"extra_prompt": "  先给结论，再给行号  "})
    assert agent_graph._extra_prompt() == "先给结论，再给行号"


def test_graph_tool_round_cap_takes_the_stricter_node():
    from app.agent import graph as agent_graph

    assert agent_graph._max_tool_rounds() == 10
    node_config.save_node("agent", {"max_tool_rounds": 8})
    node_config.save_node("run_one", {"max_tool_rounds": 5})
    assert agent_graph._max_tool_rounds() == 5


def test_prompt_builder_appends_the_extra_instruction():
    from app.agent import prompts

    plain = prompts.build_system_prompt(
        mode="project",
        file_path=None,
        start_line=None,
        end_line=None,
        selected_code=None,
        context="",
        project_name="demo",
    )
    extra = prompts.build_system_prompt(
        mode="project",
        file_path=None,
        start_line=None,
        end_line=None,
        selected_code=None,
        context="",
        project_name="demo",
        extra_prompt="只用一句话回答",
    )
    assert extra.startswith(plain)
    assert extra.endswith("只用一句话回答")
