from pydantic import BaseModel


class AgentConfig(BaseModel):
    """Agent configuration schema. See Elsa-System/templates/33-INTERFACE-CONTRACTS.md"""

    pass  # TODO: implement from interface contracts


class AgentStatus(BaseModel):
    """Agent status schema."""

    pass  # TODO: implement from interface contracts
