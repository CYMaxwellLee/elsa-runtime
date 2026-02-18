from abc import ABC, abstractmethod


class KnowledgeAccumulationProtocol(ABC):
    """Interface-first knowledge accumulation protocol.
    See Elsa-System/core/05-KNOWLEDGE-ACCUMULATION.md"""

    @abstractmethod
    async def accumulate(self, insight): ...

    @abstractmethod
    async def retrieve(self, query): ...
