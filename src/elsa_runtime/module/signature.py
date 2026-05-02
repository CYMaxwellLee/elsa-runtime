"""Signature: typed I/O contract for Module / Node.

DSPy-style (arXiv 2310.03714). A Signature binds a description to a
Pydantic input schema and a Pydantic output schema, so the LLM
invocation has a typed contract rather than a free-form prompt.

Usage:
    class ExtractCandidatesInput(BaseModel):
        emails: list[EmailRef]

    class ExtractCandidatesOutput(BaseModel):
        candidates: list[CandidateItem]

    class ExtractCandidatesSig(Signature):
        description = "Extract briefing candidates from emails"
        input_schema = ExtractCandidatesInput
        output_schema = ExtractCandidatesOutput
"""

from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel

InputT = TypeVar("InputT", bound=BaseModel)
OutputT = TypeVar("OutputT", bound=BaseModel)


# Sentinel placeholders for declarative DSPy-style field descriptions.
# Currently informational only (Pydantic Field carries the real schema),
# but keeping the symbols stable matches the DSPy mental model and lets
# downstream skills annotate fields without importing pydantic.Field.
def InputField(*, desc: str = "", **kwargs: Any) -> dict:
    return {"role": "input", "desc": desc, **kwargs}


def OutputField(*, desc: str = "", **kwargs: Any) -> dict:
    return {"role": "output", "desc": desc, **kwargs}


class Signature(Generic[InputT, OutputT]):
    """Typed I/O contract.

    Subclasses set ``description``, ``input_schema``, ``output_schema``
    as class attributes. Validation goes through Pydantic.
    """

    description: str = ""
    input_schema: type[BaseModel]
    output_schema: type[BaseModel]

    @classmethod
    def validate_input(cls, data: dict | BaseModel) -> BaseModel:
        if isinstance(data, BaseModel):
            data = data.model_dump()
        return cls.input_schema.model_validate(data)

    @classmethod
    def validate_output(cls, data: dict | BaseModel) -> BaseModel:
        if isinstance(data, BaseModel):
            data = data.model_dump()
        return cls.output_schema.model_validate(data)
