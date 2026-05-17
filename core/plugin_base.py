from abc import ABC, abstractmethod
from typing import Literal, Any
import structlog

logger = structlog.get_logger(__name__)


class HugoMcpPlugin(ABC):
    name: str
    version: str
    description: str
    requires_secret: bool

    # Set to True in subclasses that handle audit events. Default False keeps
    # backward compatibility: existing page-event-only plugins ignore audits.
    handles_audit: bool = False

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

    async def on_audit(
        self,
        audit_type: str,
        context: dict[str, Any],
    ) -> dict:
        """
        Hook for non-page events: scheduled audits, on-demand checks, etc.
        Default implementation is a no-op; plugins that participate in audits
        must set ``handles_audit = True`` and override this method.
        """
        return {"plugin": self.name, "noop": True, "reason": "audit not handled"}
