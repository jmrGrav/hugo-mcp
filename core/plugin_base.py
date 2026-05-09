from abc import ABC, abstractmethod
from typing import Literal, Any
import structlog

logger = structlog.get_logger(__name__)


class HugoMcpPlugin(ABC):
    name: str
    version: str
    description: str
    requires_secret: bool

    @abstractmethod
    def is_enabled(self, config: dict) -> bool: ...

    @abstractmethod
    def validate_config(self, config: dict) -> tuple[bool, str]: ...

    @abstractmethod
    async def on_page_event(
        self,
        event_type: Literal["created", "updated", "deleted"],
        urls: list[str],
        context: dict[str, Any],
    ) -> dict: ...
