from abc import ABC, abstractmethod


class AgentRuntime(ABC):
    """Abstract base for agent runtime environments.
    See Elsa-System/core/04-REASONING-EXECUTION-LOOP.md"""

    @abstractmethod
    async def execute(self, task): ...

    @abstractmethod
    async def health_check(self) -> bool: ...
