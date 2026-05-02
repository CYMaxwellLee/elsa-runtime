"""elsa_runtime.module: Compiled Skill framework.

Per elsa-system v3.51-A (core/C29-COMPILED-SKILL-ARCHITECTURE.md).

Three-layer borrow:
- DSPy: Module + Signature (programming model)
- LangGraph: StateGraph + nodes + edges + shared state
- Compiled AI: per-node sandwich (input schema, bounded LLM, verifier,
  audit trail)

Public API:
    Signature, InputField, OutputField        -- DSPy-style I/O
    Module                                    -- compiled skill
    StateGraph, CompiledGraph                 -- topology
    Node + 5 variants                         -- execution units
    Verifier + 3 variants + Verdict           -- output checks
    TrajectoryLogger                          -- LanceDB audit trail
    NodeExecutionError, GraphValidationError  -- exceptions
"""

from .graph import CompiledGraph, GraphValidationError, StateGraph
from .module import Module
from .node import (
    DeterministicNode,
    HybridNode,
    LLMNode,
    Node,
    NodeExecutionError,
    RouterNode,
    TerminalNode,
)
from .signature import InputField, OutputField, Signature
from .telemetry import TrajectoryLogger
from .verifier import (
    BusinessRuleVerifier,
    PydanticVerifier,
    RLVerifier,
    Verdict,
    Verifier,
)

__all__ = [
    "Signature",
    "InputField",
    "OutputField",
    "Module",
    "StateGraph",
    "CompiledGraph",
    "GraphValidationError",
    "Node",
    "DeterministicNode",
    "LLMNode",
    "HybridNode",
    "RouterNode",
    "TerminalNode",
    "NodeExecutionError",
    "Verifier",
    "PydanticVerifier",
    "BusinessRuleVerifier",
    "RLVerifier",
    "Verdict",
    "TrajectoryLogger",
]
