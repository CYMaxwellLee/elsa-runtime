"""Module: end-to-end compile + invoke + describe."""
from __future__ import annotations

import pytest
from pydantic import BaseModel

from elsa_runtime.module import (
    DeterministicNode,
    Module,
    StateGraph,
    TerminalNode,
)


class CalcState(BaseModel):
    n: int = 0
    sent: bool = False


class Plus(DeterministicNode[CalcState]):
    def __init__(self, name, k):
        super().__init__(name=name)
        self.k = k

    def run(self, state):
        state.n += self.k
        return state


class Done(TerminalNode[CalcState]):
    def run(self, state):
        state.sent = True
        return state


class CalcModule(Module):
    name = "calc"
    description = "tiny test module"
    state_schema = CalcState
    source_insights = ["insight-test-001", "insight-test-002"]

    def build_graph(self):
        g = StateGraph(CalcState)
        g.add_node(Plus("plus_one", 1))
        g.add_node(Plus("plus_ten", 10))
        g.add_node(Done(name="done"))
        g.add_edge("plus_one", "plus_ten")
        g.add_edge("plus_ten", "done")
        g.set_entry("plus_one")
        return g


def test_module_run_end_to_end():
    m = CalcModule()
    out = m.run(n=5)
    assert out["n"] == 16
    assert out["sent"] is True


def test_module_run_with_no_inputs_uses_defaults():
    m = CalcModule()
    out = m.run()
    assert out["n"] == 11
    assert out["sent"] is True


def test_module_describe_includes_source_insights_and_mermaid():
    m = CalcModule()
    desc = m.describe()
    assert "# calc" in desc
    assert "tiny test module" in desc
    assert "insight-test-001" in desc
    assert "insight-test-002" in desc
    assert "```mermaid" in desc
    assert "graph TD" in desc
    assert "plus_one" in desc and "done" in desc


def test_module_without_state_schema_raises():
    class Broken(Module):
        name = "broken"
        # state_schema deliberately not declared

        def build_graph(self):
            return StateGraph(CalcState)

    Broken.state_schema = None  # explicit
    with pytest.raises(TypeError, match="state_schema"):
        Broken()


def test_module_default_name_is_class_name():
    class NamelessModule(Module):
        state_schema = CalcState

        def build_graph(self):
            g = StateGraph(CalcState)
            g.add_node(Plus("plus_one", 1))
            g.add_node(Done(name="done"))
            g.add_edge("plus_one", "done")
            g.set_entry("plus_one")
            return g

    m = NamelessModule()
    assert m.name == "NamelessModule"
