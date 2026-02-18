from .base import AgentRuntime


class ElsaRuntime(AgentRuntime):
    """Elsa runtime implementation for Phase 4+.
    See Elsa-System/core/04-REASONING-EXECUTION-LOOP.md"""

    async def execute(self, task):
        raise NotImplementedError  # TODO: implement for Phase 4+

    async def health_check(self) -> bool:
        raise NotImplementedError  # TODO: implement for Phase 4+
