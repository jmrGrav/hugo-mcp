import httpx
import time
from typing import Literal, Any
import structlog

from core.plugin_base import HugoMcpPlugin

logger = structlog.get_logger(__name__)


class IndexNowPlugin(HugoMcpPlugin):
    name = "indexnow"
    version = "1.0.0"
    description = "Submit URLs to Bing/Yandex via IndexNow protocol"
    requires_secret = True

    def is_enabled(self, config: dict) -> bool:
        return config.get("enabled", False)

    def validate_config(self, config: dict) -> tuple[bool, str]:
        if not self.is_enabled(config):
            return True, ""
        if not config.get("key"):
            return False, "Missing required field: key"
        if not config.get("key_location"):
            return False, "Missing required field: key_location"
        if "REMPLACER" in str(config.get("key", "")):
            return False, "Default placeholder key — please configure"
        return True, ""

    async def on_page_event(
        self,
        event_type: Literal["created", "updated", "deleted"],
        urls: list[str],
        context: dict[str, Any],
    ) -> dict:
        from core.plugin_loader import registry
        config = registry.config.get("indexnow", {})

        endpoint = (config.get("endpoints") or ["https://api.indexnow.org/indexnow"])[0]
        key = config["key"]
        key_location = config["key_location"]
        host = config.get("host", "")

        payload = {
            "host": host,
            "key": key,
            "keyLocation": key_location,
            "urlList": urls,
        }

        start = time.monotonic()
        async with httpx.AsyncClient(timeout=8.0) as client:
            try:
                response = await client.post(
                    endpoint,
                    json=payload,
                    headers={"Content-Type": "application/json; charset=utf-8"},
                )
                duration_ms = int((time.monotonic() - start) * 1000)
                success = response.status_code in (200, 202)
                logger.info(
                    "plugin.indexnow.submission",
                    event_type=event_type,
                    urls_count=len(urls),
                    http_status=response.status_code,
                    duration_ms=duration_ms,
                    success=success,
                )
                return {
                    "plugin": self.name,
                    "success": success,
                    "indexer": "indexnow",
                    "urls_submitted": len(urls),
                    "http_status": response.status_code,
                    "duration_ms": duration_ms,
                }
            except Exception as e:
                duration_ms = int((time.monotonic() - start) * 1000)
                logger.error("plugin.indexnow.error", error=str(e), duration_ms=duration_ms)
                return {
                    "plugin": self.name,
                    "success": False,
                    "indexer": "indexnow",
                    "error": str(e),
                    "duration_ms": duration_ms,
                }
