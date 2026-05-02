"""Verifier: per-node output check.

Compiled AI sandwich pattern, Phase 3 of 4 (input schema, bounded LLM,
verifier, audit trail). The verifier is the deterministic safety check
that decides whether an LLM output is acceptable; if it fails, the
LLMNode regenerates with the verifier error as context (DSPy-style
self-refine).

In Phase 1-B we ship deterministic verifiers:
- PydanticVerifier: schema-only (mostly redundant since LLMNode already
  validates output, but useful as standalone or for chained nodes).
- BusinessRuleVerifier: subclass and implement check() with custom rules
  (e.g. "every fact must have evidence_id").

Phase 4+ will add RLVerifier (RL Tango: generative process-reward model
co-trained with the generator). The interface is preserved so the
runtime can swap verifiers without changing nodes.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel


@dataclass
class Verdict:
    passed: bool
    error_msg: str | None = None
    score: float | None = None  # for RL phase


class Verifier(ABC):
    @abstractmethod
    def check(self, output: Any, context: Any | None = None) -> Verdict:
        ...


class PydanticVerifier(Verifier):
    """Schema-only validation."""

    def __init__(self, schema: type[BaseModel]):
        self.schema = schema

    def check(self, output: Any, context: Any | None = None) -> Verdict:
        try:
            data = output.model_dump() if isinstance(output, BaseModel) else output
            self.schema.model_validate(data)
            return Verdict(passed=True)
        except Exception as e:
            return Verdict(passed=False, error_msg=str(e))


class BusinessRuleVerifier(Verifier):
    """Subclass and implement check() with custom business rules."""

    @abstractmethod
    def check(self, output: Any, context: Any | None = None) -> Verdict:
        ...


class RLVerifier(Verifier):
    """Phase 4+ placeholder: RL-trained generative verifier (RL Tango).

    Currently raises NotImplementedError. Architecture preserves the
    interface so nodes can swap verifier impl without code change.
    """

    def check(self, output: Any, context: Any | None = None) -> Verdict:
        raise NotImplementedError(
            "RL verifier requires Phase 4+ training infrastructure"
        )
