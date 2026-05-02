"""Signature: Pydantic input/output schema validation."""
from __future__ import annotations

import pytest
from pydantic import BaseModel, ValidationError

from elsa_runtime.module import InputField, OutputField, Signature


class SimpleIn(BaseModel):
    n: int
    label: str


class SimpleOut(BaseModel):
    doubled: int


class SimpleSig(Signature):
    description = "Double n"
    input_schema = SimpleIn
    output_schema = SimpleOut


def test_validate_input_accepts_dict():
    out = SimpleSig.validate_input({"n": 3, "label": "x"})
    assert isinstance(out, SimpleIn)
    assert out.n == 3


def test_validate_input_accepts_basemodel_instance():
    out = SimpleSig.validate_input(SimpleIn(n=4, label="y"))
    assert out.n == 4 and out.label == "y"


def test_validate_input_coerces_string_to_int():
    out = SimpleSig.validate_input({"n": "7", "label": "z"})
    assert out.n == 7


def test_validate_input_rejects_invalid():
    with pytest.raises(ValidationError):
        SimpleSig.validate_input({"n": "not-an-int", "label": "z"})


def test_validate_output_round_trip():
    out = SimpleSig.validate_output({"doubled": 6})
    assert isinstance(out, SimpleOut)
    assert out.doubled == 6


def test_validate_output_rejects_missing_field():
    with pytest.raises(ValidationError):
        SimpleSig.validate_output({})


def test_input_field_output_field_helpers_return_dict():
    a = InputField(desc="raw email list")
    b = OutputField(desc="extracted candidates")
    assert a["role"] == "input" and a["desc"] == "raw email list"
    assert b["role"] == "output" and b["desc"] == "extracted candidates"
