from pydantic import BaseModel


class Insight(BaseModel):
    """Insight schema. See Elsa-System/templates/33-INTERFACE-CONTRACTS.md"""

    pass  # TODO: implement from interface contracts


class InsightLifecycle(BaseModel):
    """Insight lifecycle schema."""

    pass  # TODO: implement from interface contracts


class QualityScore(BaseModel):
    """Quality score schema."""

    pass  # TODO: implement from interface contracts
