from .base import AgentRuntime


class OpenClawRuntime(AgentRuntime):
    """OpenClaw runtime implementation for Phase 0-2.
    See Elsa-System/core/04-REASONING-EXECUTION-LOOP.md"""

    async def execute(self, task):
        raise NotImplementedError  # TODO: implement for Phase 0-2

    async def health_check(self) -> bool:
        raise NotImplementedError  # TODO: implement for Phase 0-2
