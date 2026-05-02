"""Module: skill = compiled Python class (DSPy-style).

Per C29 §3.4 + PATCH-v3.51-A §3.4.

A Module owns:
- a Pydantic ``state_schema``
- a ``build_graph()`` returning a StateGraph[state_schema]
- ``source_insights``: provenance trail (which insights / incidents /
  user feedback this module compiled from)
- a compiled graph (built at __init__)

The skill markdown file becomes a 3-line stub that delegates to
``python -m <skill_module_path>``; the LLM no longer sees workflow
prose.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel

from .graph import CompiledGraph, StateGraph


class Module(ABC):
    """Skill = Compiled Module.

    Subclass contract:
        name: str (class attr)
        description: str (class attr)
        state_schema: type[BaseModel] (class attr)
        source_insights: list[str] (class attr; provenance)

        def build_graph(self) -> StateGraph[state_schema]: ...
    """

    name: str = ""
    description: str = ""
    state_schema: type[BaseModel]
    source_insights: list[str] = []

    def __init__(self) -> None:
        if not self.name:
            self.name = type(self).__name__
        if not hasattr(self, "state_schema") or self.state_schema is None:
            raise TypeError(
                f"{type(self).__name__} must declare state_schema"
            )
        self.graph: StateGraph = self.build_graph()
        self.compiled: CompiledGraph = self.graph.compile()

    @abstractmethod
    def build_graph(self) -> StateGraph:
        ...

    def run(self, **inputs: Any) -> dict:
        """Execute module from initial inputs to terminal state.

        Returns the final state as a dict (model_dump).
        """
        initial_state = self.state_schema(**inputs)
        final_state = self.compiled.invoke(initial_state)
        return final_state.model_dump()

    def describe(self) -> str:
        """Auto-generate a human-readable markdown description.

        Used by the main user to review what a module compiles down to:
        the source insights it was internalised from, plus its workflow
        graph in mermaid.
        """
        lines = [
            f"# {self.name}",
            "",
            self.description or "(no description)",
            "",
            "## Compiled from insights",
            "",
        ]
        if self.source_insights:
            for ins in self.source_insights:
                lines.append(f"- {ins}")
        else:
            lines.append("_(none recorded)_")
        lines.extend(
            [
                "",
                "## Workflow graph",
                "",
                "```mermaid",
                self.graph.visualize(),
                "```",
                "",
            ]
        )
        return "\n".join(lines)
