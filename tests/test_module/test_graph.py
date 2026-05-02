"""StateGraph: linear / parallel / conditional / cycle / mermaid."""
from __future__ import annotations

import pytest
from pydantic import BaseModel

from elsa_runtime.module import (
    DeterministicNode,
    GraphValidationError,
    RouterNode,
    StateGraph,
    TerminalNode,
)


class S(BaseModel):
    n: int = 0
    history: list[str] = []
    sent: bool = False
    _next_node: str | None = None


class Add(DeterministicNode[S]):
    def __init__(self, name, k):
        super().__init__(name=name)
        self.k = k

    def run(self, state):
        state.n += self.k
        state.history.append(self.name)
        return state


class Mul(DeterministicNode[S]):
    def __init__(self, name, k):
        super().__init__(name=name)
        self.k = k

    def run(self, state):
        state.n *= self.k
        state.history.append(self.name)
        return state


class End(TerminalNode[S]):
    def run(self, state):
        state.sent = True
        state.history.append(self.name)
        return state


# --- linear topology ---


def test_linear_graph():
    g = StateGraph(S)
    g.add_node(Add("a", 1))
    g.add_node(Add("b", 10))
    g.add_node(End(name="end"))
    g.add_edge("a", "b")
    g.add_edge("b", "end")
    g.set_entry("a")
    compiled = g.compile()
    out = compiled.invoke(S())
    assert out.n == 11
    assert out.history == ["a", "b", "end"]
    assert out.sent is True


# --- parallel topology ---


def test_parallel_graph_with_explicit_converge():
    g = StateGraph(S)
    g.add_node(Add("source", 0))
    g.add_node(Add("p1", 1))
    g.add_node(Add("p2", 2))
    g.add_node(Add("p3", 3))
    g.add_node(End(name="end"))
    g.add_parallel_then("source", ["p1", "p2", "p3"], converge="end")
    g.set_entry("source")
    compiled = g.compile()
    out = compiled.invoke(S(n=0))
    # All three parallel additions run sequentially in Phase 0 impl.
    assert out.n == 0 + 1 + 2 + 3
    assert set(out.history[:1]) == {"source"}
    assert "end" in out.history


# --- conditional routing ---


class FlagState(BaseModel):
    flag: bool = False
    n: int = 0
    sent: bool = False
    _next_node: str | None = None


class FlagRouter(RouterNode[FlagState]):
    routes = {"yes": "yes_branch", "no": "no_branch"}

    def select_route(self, state):
        return "yes" if state.flag else "no"


class FlagAdd(DeterministicNode[FlagState]):
    def __init__(self, name, k):
        super().__init__(name=name)
        self.k = k

    def run(self, state):
        state.n += self.k
        return state


class FlagEnd(TerminalNode[FlagState]):
    def run(self, state):
        state.sent = True
        return state


def _build_flag_graph():
    g = StateGraph(FlagState)
    g.add_node(FlagRouter(name="router"))
    g.add_node(FlagAdd("yes_branch", 100))
    g.add_node(FlagAdd("no_branch", 1))
    g.add_node(FlagEnd(name="end"))
    g.add_edge("yes_branch", "end")
    g.add_edge("no_branch", "end")
    g.set_entry("router")
    return g.compile()


def test_router_takes_yes_branch():
    out = _build_flag_graph().invoke(FlagState(flag=True))
    assert out.n == 100
    assert out.sent is True


def test_router_takes_no_branch():
    out = _build_flag_graph().invoke(FlagState(flag=False))
    assert out.n == 1
    assert out.sent is True


# --- validation errors ---


def test_compile_without_entry_raises():
    g = StateGraph(S)
    g.add_node(End(name="end"))
    with pytest.raises(GraphValidationError, match="no entry"):
        g.compile()


def test_compile_without_terminal_raises():
    g = StateGraph(S)
    g.add_node(Add("a", 1))
    g.set_entry("a")
    with pytest.raises(GraphValidationError, match="no terminal"):
        g.compile()


def test_compile_unreachable_node_raises():
    g = StateGraph(S)
    g.add_node(Add("a", 1))
    g.add_node(Add("orphan", 99))
    g.add_node(End(name="end"))
    g.add_edge("a", "end")
    g.set_entry("a")
    with pytest.raises(GraphValidationError, match="Unreachable"):
        g.compile()


def test_compile_cycle_through_hard_edges_raises():
    g = StateGraph(S)
    g.add_node(Add("a", 1))
    g.add_node(Add("b", 1))
    g.add_node(End(name="end"))
    g.add_edge("a", "b")
    g.add_edge("b", "a")  # cycle
    g.add_edge("a", "end")
    g.set_entry("a")
    with pytest.raises(GraphValidationError, match="Cycle"):
        g.compile()


def test_duplicate_node_name_raises():
    g = StateGraph(S)
    g.add_node(Add("a", 1))
    with pytest.raises(GraphValidationError, match="Duplicate"):
        g.add_node(Add("a", 2))


def test_edge_to_unknown_node_raises_on_compile():
    g = StateGraph(S)
    g.add_node(Add("a", 1))
    g.add_node(End(name="end"))
    g.add_edge("a", "missing")
    g.add_edge("a", "end")
    g.set_entry("a")
    with pytest.raises(GraphValidationError, match="unknown node"):
        g.compile()


# --- mermaid visualization ---


def test_visualize_returns_mermaid_with_nodes_and_edges():
    g = StateGraph(S)
    g.add_node(Add("a", 1))
    g.add_node(End(name="end_node"))
    g.add_edge("a", "end_node")
    g.set_entry("a")
    compiled = g.compile()  # noqa: F841
    diagram = g.visualize()
    assert diagram.startswith("graph TD")
    assert "a[a]" in diagram
    assert "end_node[[end_node]]" in diagram
    assert "a --> end_node" in diagram


def test_visualize_uses_router_diamond_shape():
    g = StateGraph(FlagState)
    g.add_node(FlagRouter(name="router"))
    g.add_node(FlagAdd("yes_branch", 1))
    g.add_node(FlagEnd(name="end"))
    g.add_edge("yes_branch", "end")
    g.set_entry("router")
    diagram = g.visualize()
    assert "router{router}" in diagram
