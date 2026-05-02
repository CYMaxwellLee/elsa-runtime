"""Node: execution unit in a StateGraph.

Five variants, all share telemetry-wrapped __call__:

- DeterministicNode : pure Python, no LLM.
- LLMNode           : Compiled AI sandwich (input schema, bounded LLM,
                      verifier, retry on failure).
- HybridNode        : free-form mix of deterministic + LLM (subclass
                      writes run() however it wants; for non-trivial
                      orchestrations that don't fit pure LLMNode).
- RouterNode        : conditional routing; sets state._next_node.
- TerminalNode      : graph terminal; performs final action.

Per C29 §3.2 + PATCH-v3.51-A §3.2.

Telemetry note: the base ``__call__`` wraps run() with enter/exit
logging. RouterNode overrides __call__ to additionally log the route
decision (see RouterNode.run + log_route in telemetry).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Generic, TypeVar

from pydantic import BaseModel

from .signature import Signature
from .telemetry import TrajectoryLogger
from .verifier import Verifier

StateT = TypeVar("StateT", bound=BaseModel)


class NodeExecutionError(RuntimeError):
    """Raised when a node fails after all retries."""


class Node(ABC, Generic[StateT]):
    """Base node. Subclass via the 5 variants below."""

    name: str = ""
    inputs: list[str] = []
    outputs: list[str] = []
    is_llm: bool | str = False
    is_terminal: bool = False

    def __init__(
        self,
        name: str | None = None,
        telemetry: TrajectoryLogger | None = None,
    ):
        # Allow subclasses to set ``name`` as a class attribute. If a
        # name is passed in __init__ it overrides.
        if name is not None:
            self.name = name
        if not self.name:
            self.name = type(self).__name__
        self.telemetry = telemetry

    @abstractmethod
    def run(self, state: StateT) -> StateT:
        """Execute the node and return the (mutated or new) state."""

    def __call__(self, state: StateT) -> StateT:
        if self.telemetry:
            self.telemetry.log_enter(self.name, state)
        try:
            new_state = self.run(state)
        except Exception as e:
            if self.telemetry:
                self.telemetry.log_exit(
                    self.name, state, success=False, error=str(e)
                )
            raise
        if self.telemetry:
            self.telemetry.log_exit(self.name, new_state, success=True)
        return new_state


class DeterministicNode(Node[StateT]):
    """Pure Python, no LLM."""

    is_llm = False

    @abstractmethod
    def run(self, state: StateT) -> StateT:
        ...


class LLMNode(Node[StateT]):
    """LLM call wrapped in Compiled AI sandwich.

    Subclass must:
    - set ``signature`` (class attr) or pass at __init__
    - implement ``_call_llm(validated_input, error_context)`` returning
      a dict (raw LLM output, will be schema-validated)

    The sandwich loop:
        Phase 1: input schema validation
        Phase 2: LLM call (bounded output schema)
        Phase 3: schema check, then verifier (if any)
        Phase 4: retry on schema or verifier failure (max_retries)
    """

    is_llm = True
    signature: type[Signature] | Signature | None = None
    verifier: Verifier | None = None
    max_retries: int = 3

    def __init__(
        self,
        name: str | None = None,
        signature: type[Signature] | Signature | None = None,
        verifier: Verifier | None = None,
        max_retries: int | None = None,
        telemetry: TrajectoryLogger | None = None,
    ):
        super().__init__(name=name, telemetry=telemetry)
        if signature is not None:
            self.signature = signature
        if verifier is not None:
            self.verifier = verifier
        if max_retries is not None:
            self.max_retries = max_retries

    def run(self, state: StateT) -> StateT:
        if self.signature is None:
            raise NodeExecutionError(
                f"{self.name}: LLMNode requires a Signature"
            )

        sig = self.signature
        validated_input = sig.validate_input(self._extract_inputs(state))

        last_error: str | None = None
        for _attempt in range(self.max_retries):
            raw_output = self._call_llm(validated_input, error_context=last_error)

            try:
                output = sig.validate_output(raw_output)
            except Exception as e:
                last_error = f"Schema validation failed: {e}"
                continue

            if self.verifier is not None:
                verdict = self.verifier.check(output, context=validated_input)
                if not verdict.passed:
                    last_error = verdict.error_msg or "verifier rejected output"
                    continue

            return self._merge_state(state, output)

        raise NodeExecutionError(
            f"{self.name}: failed after {self.max_retries} retries. "
            f"Last error: {last_error}"
        )

    # --- subclass extension points ---

    def _call_llm(self, inputs: BaseModel, error_context: str | None = None) -> Any:
        """Subclass implements the actual LLM invocation.

        Should return a dict / Pydantic-coercible payload that will be
        validated against ``signature.output_schema``.
        """
        raise NotImplementedError(
            f"{type(self).__name__}._call_llm must be implemented by subclass"
        )

    def _extract_inputs(self, state: StateT) -> dict:
        return {k: getattr(state, k) for k in self.inputs}

    def _merge_state(self, state: StateT, output: BaseModel) -> StateT:
        for k in self.outputs:
            if hasattr(output, k):
                setattr(state, k, getattr(output, k))
        return state


class HybridNode(Node[StateT]):
    """Custom hybrid node.

    Per main user (5/2 chat): "node 可以有彈性的實作方式, 不一定是
    deterministic 或 llm, 也可以 hybrid". The subclass implements run()
    and may freely combine deterministic logic with bounded LLM calls.
    """

    is_llm = "hybrid"

    @abstractmethod
    def run(self, state: StateT) -> StateT:
        ...


class RouterNode(Node[StateT]):
    """Conditional routing.

    Subclass implements ``select_route(state) -> str`` returning a key
    in ``self.routes``; the corresponding next-node name is written to
    ``state._next_node``.

    Phase 1-B: heuristic. Phase 3: supervised. Phase 4+: RL policy.
    """

    is_llm = False
    routes: dict[str, str] = {}

    def __init__(
        self,
        name: str | None = None,
        routes: dict[str, str] | None = None,
        telemetry: TrajectoryLogger | None = None,
    ):
        super().__init__(name=name, telemetry=telemetry)
        if routes is not None:
            self.routes = routes

    @abstractmethod
    def select_route(self, state: StateT) -> str:
        ...

    def run(self, state: StateT) -> StateT:
        route = self.select_route(state)
        if route not in self.routes:
            raise NodeExecutionError(
                f"{self.name}: select_route returned unknown route '{route}' "
                f"(known: {sorted(self.routes)})"
            )
        # Use object.__setattr__ so this works on Pydantic models with
        # arbitrary configurations as long as the field exists.
        if hasattr(state, "_next_node"):
            object.__setattr__(state, "_next_node", self.routes[route])
        else:
            # Fall back to setattr on the model __dict__.
            try:
                setattr(state, "_next_node", self.routes[route])
            except Exception as e:
                raise NodeExecutionError(
                    f"{self.name}: state has no _next_node field "
                    f"(add `_next_node: str | None = None` to state schema)"
                ) from e
        if self.telemetry:
            self.telemetry.log_route(self.name, route)
        return state


class TerminalNode(Node[StateT]):
    """Graph terminal. Performs the final action (e.g. send_telegram).

    Critical: graph topology guarantees all upstream nodes have run
    before reaching here. The LLM cannot reach a TerminalNode without
    going through every required upstream node.
    """

    is_llm = False
    is_terminal = True

    @abstractmethod
    def run(self, state: StateT) -> StateT:
        ...
