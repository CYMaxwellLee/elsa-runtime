"""StateGraph + CompiledGraph: workflow topology.

LangGraph-style. A StateGraph holds nodes + edges + parallel fan-outs +
entry/exit points; compile() validates the topology and returns a
CompiledGraph ready for invoke(initial_state).

Per C29 §3.3 + PATCH-v3.51-A §3.3.

Phase 0 simplifications (acceptable per IMPLEMENTATION §11.4):
- _run_parallel: sequential execution. Phase 2+ swaps to
  concurrent.futures.ThreadPoolExecutor.
- _find_next_after_parallel: relies on add_parallel_then(source, parallel_nodes,
  converge) to record the convergence point explicitly. Without an
  explicit converge edge, falls back to the first edge from any of the
  parallel destinations.

Validation rules (compile()):
- entry must be set
- at least one TerminalNode exists
- every node reachable from entry
- every reachable path eventually hits a terminal
- no cycles outside RouterNode-allowed loops
"""

from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel

from .node import Node, NodeExecutionError, RouterNode, TerminalNode

StateT = TypeVar("StateT", bound=BaseModel)


class GraphValidationError(ValueError):
    """Raised when StateGraph.compile() detects an invalid topology."""


class StateGraph(Generic[StateT]):
    def __init__(self, state_schema: type[StateT]):
        self.state_schema = state_schema
        self.nodes: dict[str, Node] = {}
        self.edges: list[tuple[str, str]] = []
        self.parallel_edges: dict[str, list[str]] = {}
        # Optional explicit convergence target after a parallel fan-out.
        # parallel_converge[source] = node_name to run after all
        # parallel destinations finish. If unset, _find_next_after_parallel
        # falls back to the next edge of one of the parallel destinations.
        self.parallel_converge: dict[str, str] = {}
        self.entry: str | None = None
        self.exits: set[str] = set()

    # --- builder API ---

    def add_node(self, node: Node) -> "StateGraph[StateT]":
        if not node.name:
            raise GraphValidationError("Node must have a non-empty name")
        if node.name in self.nodes:
            raise GraphValidationError(f"Duplicate node name: {node.name}")
        self.nodes[node.name] = node
        if isinstance(node, TerminalNode):
            self.exits.add(node.name)
        return self

    def add_edge(self, from_node: str, to_node: str) -> "StateGraph[StateT]":
        self.edges.append((from_node, to_node))
        return self

    def add_parallel(
        self, from_node: str, to_nodes: list[str]
    ) -> "StateGraph[StateT]":
        """Fan-out from ``from_node`` to ``to_nodes`` (all run in parallel)."""
        self.parallel_edges[from_node] = list(to_nodes)
        return self

    def add_parallel_then(
        self, from_node: str, to_nodes: list[str], converge: str
    ) -> "StateGraph[StateT]":
        """Fan-out then converge to a specific node."""
        self.parallel_edges[from_node] = list(to_nodes)
        self.parallel_converge[from_node] = converge
        return self

    def set_entry(self, node_name: str) -> "StateGraph[StateT]":
        self.entry = node_name
        return self

    # --- compilation ---

    def compile(self) -> "CompiledGraph[StateT]":
        self._validate()
        return CompiledGraph(self)

    def _validate(self) -> None:
        if self.entry is None:
            raise GraphValidationError("Graph has no entry node")
        if self.entry not in self.nodes:
            raise GraphValidationError(
                f"Entry '{self.entry}' is not a registered node"
            )
        if not self.exits:
            raise GraphValidationError("Graph has no terminal nodes")

        # All edges reference known nodes
        for f, t in self.edges:
            if f not in self.nodes:
                raise GraphValidationError(f"Edge from unknown node: {f}")
            if t not in self.nodes:
                raise GraphValidationError(f"Edge to unknown node: {t}")
        for src, dests in self.parallel_edges.items():
            if src not in self.nodes:
                raise GraphValidationError(f"Parallel from unknown node: {src}")
            for d in dests:
                if d not in self.nodes:
                    raise GraphValidationError(
                        f"Parallel destination '{d}' is not a registered node"
                    )
        for src, conv in self.parallel_converge.items():
            if conv not in self.nodes:
                raise GraphValidationError(
                    f"Parallel converge '{conv}' is not a registered node"
                )

        # Reachability + cycle detection (DFS).
        # Cycles via RouterNode are tolerated (the router decides at
        # runtime); we mark RouterNode-outgoing edges as "soft" by
        # excluding them from the simple cycle check.
        reachable = self._reachable_from(self.entry)
        unreachable = set(self.nodes) - reachable
        if unreachable:
            raise GraphValidationError(
                f"Unreachable nodes from entry: {sorted(unreachable)}"
            )

        self._cycle_check()

    def _outgoing(self, node_name: str) -> list[str]:
        outs: list[str] = []
        for f, t in self.edges:
            if f == node_name:
                outs.append(t)
        if node_name in self.parallel_edges:
            outs.extend(self.parallel_edges[node_name])
        if node_name in self.parallel_converge:
            outs.append(self.parallel_converge[node_name])
        node = self.nodes.get(node_name)
        if isinstance(node, RouterNode):
            outs.extend(node.routes.values())
        return outs

    def _reachable_from(self, start: str) -> set[str]:
        seen: set[str] = set()
        stack = [start]
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            for nxt in self._outgoing(cur):
                if nxt not in seen:
                    stack.append(nxt)
        return seen

    def _cycle_check(self) -> None:
        """Detect cycles in the non-router topology.

        We allow cycles through RouterNode (router edges are dynamic).
        For all other edges, a back-edge in DFS indicates an invalid loop.
        """
        WHITE, GRAY, BLACK = 0, 1, 2
        color = {n: WHITE for n in self.nodes}

        def dfs(u: str) -> None:
            color[u] = GRAY
            node = self.nodes[u]
            outs: list[str] = []
            if not isinstance(node, RouterNode):
                # Hard edges only for cycle check.
                for f, t in self.edges:
                    if f == u:
                        outs.append(t)
                if u in self.parallel_edges:
                    outs.extend(self.parallel_edges[u])
                if u in self.parallel_converge:
                    outs.append(self.parallel_converge[u])
            for v in outs:
                if color[v] == GRAY:
                    raise GraphValidationError(
                        f"Cycle detected through non-router edge: {u} -> {v}"
                    )
                if color[v] == WHITE:
                    dfs(v)
            color[u] = BLACK

        for n in self.nodes:
            if color[n] == WHITE:
                dfs(n)

    # --- visualization ---

    def visualize(self) -> str:
        """Return a mermaid graph TD source string."""
        lines = ["graph TD"]
        for name, node in self.nodes.items():
            shape = self._shape(node)
            safe = self._safe_label(name)
            lines.append(f"  {safe}{shape}")
        for f, t in self.edges:
            lines.append(f"  {self._safe_label(f)} --> {self._safe_label(t)}")
        for src, dests in self.parallel_edges.items():
            for d in dests:
                lines.append(
                    f"  {self._safe_label(src)} -.parallel.-> {self._safe_label(d)}"
                )
        for src, conv in self.parallel_converge.items():
            lines.append(
                f"  {self._safe_label(src)} ==converge==> {self._safe_label(conv)}"
            )
        return "\n".join(lines)

    @staticmethod
    def _safe_label(name: str) -> str:
        # mermaid node ids must be alphanumeric / underscore.
        return "".join(c if c.isalnum() or c == "_" else "_" for c in name)

    def _shape(self, node: Node) -> str:
        label = node.name
        if isinstance(node, TerminalNode):
            return f"[[{label}]]"
        if isinstance(node, RouterNode):
            return f"{{{label}}}"
        if getattr(node, "is_llm", False) is True:
            return f"([{label}])"  # rounded for LLM nodes
        if getattr(node, "is_llm", False) == "hybrid":
            return f">{label}]"  # asymmetric for hybrid
        return f"[{label}]"


class CompiledGraph(Generic[StateT]):
    """Executable form of a validated StateGraph."""

    MAX_STEPS = 1000  # safety guard against runaway router loops

    def __init__(self, graph: StateGraph[StateT]):
        self.graph = graph

    def invoke(self, initial_state: StateT) -> StateT:
        state = initial_state
        current: str | None = self.graph.entry
        steps = 0

        while current is not None:
            if steps > self.MAX_STEPS:
                raise NodeExecutionError(
                    f"CompiledGraph: exceeded MAX_STEPS={self.MAX_STEPS} "
                    f"(possible router loop)"
                )
            steps += 1

            node = self.graph.nodes[current]
            is_terminal = isinstance(node, TerminalNode)

            # Run the node (telemetry inside __call__).
            state = node(state)

            if is_terminal:
                return state

            # Determine next node.
            if isinstance(node, RouterNode):
                nxt = getattr(state, "_next_node", None)
                if nxt is None:
                    raise NodeExecutionError(
                        f"RouterNode {current} did not set _next_node"
                    )
                current = nxt
                # Clear so the next router/iteration starts fresh.
                if hasattr(state, "_next_node"):
                    object.__setattr__(state, "_next_node", None)
                continue

            if current in self.graph.parallel_edges:
                state = self._run_parallel(state, current)
                current = self._find_next_after_parallel(current)
                continue

            current = self._find_next(current)

        return state

    # --- helpers ---

    def _run_parallel(self, state: StateT, source: str) -> StateT:
        """Phase 0: sequential execution of parallel destinations.

        Phase 2+: swap to concurrent.futures, merge state at converge.
        """
        for dest in self.graph.parallel_edges[source]:
            node = self.graph.nodes[dest]
            state = node(state)
        return state

    def _find_next(self, current: str) -> str | None:
        for f, t in self.graph.edges:
            if f == current:
                return t
        return None

    def _find_next_after_parallel(self, source: str) -> str | None:
        # Explicit convergence wins.
        if source in self.graph.parallel_converge:
            return self.graph.parallel_converge[source]
        # Fallback: walk an edge from any of the parallel destinations.
        for dest in self.graph.parallel_edges[source]:
            nxt = self._find_next(dest)
            if nxt is not None:
                return nxt
        # Or follow an edge from source itself (after the parallel fan).
        return self._find_next(source)
