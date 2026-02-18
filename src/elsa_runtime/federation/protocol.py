from abc import ABC, abstractmethod


class FederationProtocol(ABC):
    """Interface-first federation protocol for Phase 3+.
    See Elsa-System/core/08-FEDERATION.md"""

    @abstractmethod
    async def send(self, message): ...

    @abstractmethod
    async def receive(self): ...
