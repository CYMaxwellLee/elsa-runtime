"""Node: deterministic / LLM (mocked) / router / terminal behaviour."""
from __future__ import annotations

import pytest
from pydantic import BaseModel

from elsa_runtime.module import (
    DeterministicNode,
    LLMNode,
    NodeExecutionError,
    RouterNode,
    Signature,
    TerminalNode,
    Verdict,
    Verifier,
)


class CounterState(BaseModel):
    n: int = 0
    history: list[str] = []
    sent: bool = False
    _next_node: str | None = None


# --- DeterministicNode ---


class AddOne(DeterministicNode[CounterState]):
    name = "add_one"

    def run(self, state):
        state.n += 1
        state.history.append(self.name)
        return state


class Multiply(DeterministicNode[CounterState]):
    def run(self, state):
        state.n *= 3
        state.history.append(self.name)
        return state


def test_deterministic_node_mutates_state():
    s = CounterState(n=2)
    out = AddOne()(s)
    assert out.n == 3
    assert out.history == ["add_one"]


def test_deterministic_node_default_name_is_class_name():
    node = Multiply()
    assert node.name == "Multiply"


def test_node_call_propagates_exception():
    class Boom(DeterministicNode[CounterState]):
        name = "boom"

        def run(self, state):
            raise RuntimeError("planned")

    with pytest.raises(RuntimeError, match="planned"):
        Boom()(CounterState())


# --- LLMNode (mocked _call_llm) ---


class LLMIn(BaseModel):
    n: int


class LLMOut(BaseModel):
    answer: int


class DoubleSig(Signature):
    description = "double n"
    input_schema = LLMIn
    output_schema = LLMOut


class StateForLLM(BaseModel):
    n: int = 0
    answer: int = 0


class GoodDouble(LLMNode[StateForLLM]):
    name = "good_double"
    inputs = ["n"]
    outputs = ["answer"]
    signature = DoubleSig

    def _call_llm(self, inputs, error_context=None):
        return {"answer": inputs.n * 2}


def test_llm_node_happy_path():
    out = GoodDouble()(StateForLLM(n=5))
    assert out.answer == 10


class FlakyThenGood(LLMNode[StateForLLM]):
    name = "flaky"
    inputs = ["n"]
    outputs = ["answer"]
    signature = DoubleSig

    def __init__(self):
        super().__init__()
        self._calls = 0

    def _call_llm(self, inputs, error_context=None):
        self._calls += 1
        if self._calls < 3:
            return {"wrong_field": "nope"}  # schema-invalid -> retry
        return {"answer": 42}


def test_llm_node_retries_on_schema_failure():
    node = FlakyThenGood()
    out = node(StateForLLM(n=21))
    assert out.answer == 42
    assert node._calls == 3


class AlwaysWrongSchema(LLMNode[StateForLLM]):
    name = "always_wrong"
    inputs = ["n"]
    outputs = ["answer"]
    signature = DoubleSig
    max_retries = 2

    def _call_llm(self, inputs, error_context=None):
        return {"unrelated": "junk"}


def test_llm_node_raises_after_exhausted_retries():
    with pytest.raises(NodeExecutionError, match="failed after 2 retries"):
        AlwaysWrongSchema()(StateForLLM(n=1))


class StrictVerifier(Verifier):
    def check(self, output, context=None):
        if output.answer == 42:
            return Verdict(passed=True)
        return Verdict(passed=False, error_msg=f"expected 42 got {output.answer}")


class FailsVerifierFirstThenPasses(LLMNode[StateForLLM]):
    name = "verifier_retry"
    inputs = ["n"]
    outputs = ["answer"]
    signature = DoubleSig

    def __init__(self):
        super().__init__(verifier=StrictVerifier())
        self._calls = 0

    def _call_llm(self, inputs, error_context=None):
        self._calls += 1
        # First call returns valid schema but wrong value; second returns 42.
        return {"answer": 7 if self._calls == 1 else 42}


def test_llm_node_verifier_retry_path():
    node = FailsVerifierFirstThenPasses()
    out = node(StateForLLM(n=99))
    assert out.answer == 42
    assert node._calls == 2


class BareLLM(LLMNode[StateForLLM]):
    name = "bare"
    inputs = ["n"]
    outputs = ["answer"]
    # signature deliberately unset


def test_llm_node_without_signature_raises():
    with pytest.raises(NodeExecutionError, match="requires a Signature"):
        BareLLM()(StateForLLM(n=1))


def test_llm_node_default_call_llm_raises_not_implemented():
    class NeedsCallLLM(LLMNode[StateForLLM]):
        name = "needs"
        inputs = ["n"]
        outputs = ["answer"]
        signature = DoubleSig

    # Override run path: just check _call_llm raises NotImplementedError directly.
    node = NeedsCallLLM()
    with pytest.raises(NotImplementedError):
        LLMNode._call_llm(node, LLMIn(n=1))


# --- RouterNode ---


class StateWithRoute(BaseModel):
    flag: bool = False
    _next_node: str | None = None


class FlagRouter(RouterNode[StateWithRoute]):
    name = "flag_router"
    routes = {"yes": "y_node", "no": "n_node"}

    def select_route(self, state):
        return "yes" if state.flag else "no"


def test_router_sets_next_node_when_true():
    s = StateWithRoute(flag=True)
    out = FlagRouter()(s)
    assert out._next_node == "y_node"


def test_router_sets_next_node_when_false():
    s = StateWithRoute(flag=False)
    out = FlagRouter()(s)
    assert out._next_node == "n_node"


class BadRouter(RouterNode[StateWithRoute]):
    name = "bad_router"
    routes = {"a": "a_node"}

    def select_route(self, state):
        return "z"  # not in routes


def test_router_unknown_route_raises():
    with pytest.raises(NodeExecutionError, match="unknown route"):
        BadRouter()(StateWithRoute())


# --- TerminalNode ---


class FinishState(BaseModel):
    sent: bool = False


class Finish(TerminalNode[FinishState]):
    name = "finish"

    def run(self, state):
        state.sent = True
        return state


def test_terminal_node_mutates_state_and_marks_terminal():
    out = Finish()(FinishState())
    assert out.sent is True
    assert Finish.is_terminal is True
