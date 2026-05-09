from typing import Literal, Any
from core.plugin_base import HugoMcpPlugin


class TemplatePlugin(HugoMcpPlugin):
    name = "template"
    version = "1.0.0"
    description = "Template plugin — copy this folder and adapt"
    requires_secret = False

    def is_enabled(self, config: dict) -> bool:
        return config.get("enabled", False)

    def validate_config(self, config: dict) -> tuple[bool, str]:
        return True, ""

    async def on_page_event(
        self,
        event_type: Literal["created", "updated", "deleted"],
        urls: list[str],
        context: dict[str, Any],
    ) -> dict:
        return {"plugin": self.name, "success": True, "event_type": event_type, "urls_count": len(urls)}
