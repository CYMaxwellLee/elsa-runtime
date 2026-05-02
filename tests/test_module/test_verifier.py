"""Verifier: pydantic / business rule / RL placeholder."""
from __future__ import annotations

import pytest
from pydantic import BaseModel

from elsa_runtime.module import (
    BusinessRuleVerifier,
    PydanticVerifier,
    RLVerifier,
    Verdict,
)


class Item(BaseModel):
    topic: str
    evidence_id: str | None = None


class Container(BaseModel):
    items: list[Item]


def test_pydantic_verifier_passes_for_matching_schema():
    v = PydanticVerifier(Container)
    obj = Container(items=[Item(topic="x", evidence_id="thread_1")])
    verdict = v.check(obj)
    assert verdict.passed is True


def test_pydantic_verifier_rejects_dict_with_missing_field():
    v = PydanticVerifier(Container)
    bad = {"items": [{"topic": "x"}, {"evidence_id": "thread_2"}]}
    # second item missing topic; pydantic will reject
    verdict = v.check(bad)
    assert verdict.passed is False
    assert verdict.error_msg


class EvidenceAttachedVerifier(BusinessRuleVerifier):
    def check(self, output, context=None):
        for item in output.items:
            if not item.evidence_id:
                return Verdict(
                    passed=False,
                    error_msg=f"Item {item.topic} missing evidence_id",
                )
        return Verdict(passed=True)


def test_business_rule_verifier_subclass_passes_when_all_have_evidence():
    v = EvidenceAttachedVerifier()
    obj = Container(items=[Item(topic="a", evidence_id="thread_1")])
    assert v.check(obj).passed is True


def test_business_rule_verifier_subclass_rejects_missing_evidence():
    v = EvidenceAttachedVerifier()
    obj = Container(
        items=[
            Item(topic="ok", evidence_id="thread_1"),
            Item(topic="bad", evidence_id=None),
        ]
    )
    verdict = v.check(obj)
    assert verdict.passed is False
    assert "bad" in verdict.error_msg


def test_rl_verifier_raises_not_implemented():
    v = RLVerifier()
    with pytest.raises(NotImplementedError, match="Phase 4"):
        v.check(Container(items=[]))
