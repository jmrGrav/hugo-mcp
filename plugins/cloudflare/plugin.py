import os
import time
from typing import Literal, Any
import httpx
import structlog

from core.plugin_base import HugoMcpPlugin

logger = structlog.get_logger(__name__)

CF_API = "https://api.cloudflare.com/client/v4/zones/{zone_id}/purge_cache"


class CloudflarePlugin(HugoMcpPlugin):
    name = "cloudflare"
    version = "1.0.0"
    description = "Cloudflare cache purge on page events (full / partial / smart)"
    requires_secret = False

    def is_enabled(self, config: dict) -> bool:
        return config.get("enabled", False)

    def validate_config(self, config: dict) -> tuple[bool, str]:
        if not self.is_enabled(config):
            return True, ""
        token_env = config.get("api_token_env", "CF_TOKEN")
        token = os.environ.get(token_env, "")
        if not token:
            return False, f"CF API token missing — env var {token_env!r} is not set"
        if not config.get("zone_id"):
            return False, "Missing zone_id in cloudflare config"
        if not config.get("base_url"):
            return False, "Missing base_url in cloudflare config"
        return True, ""

    def _get_token(self, config: dict) -> str:
        return os.environ.get(config.get("api_token_env", "CF_TOKEN"), "")

    async def on_page_event(
        self,
        event_type: Literal["created", "updated", "deleted"],
        urls: list[str],
        context: dict[str, Any],
    ) -> dict:
        from core.plugin_loader import registry
        config = registry.config.get("cloudflare", {})

        token = self._get_token(config)
        zone_id = config["zone_id"]
        base_url = config["base_url"].rstrip("/")
        mode = config.get("mode", "smart")
        related_urls = config.get("related_urls", [])

        if not urls:
            logger.debug("plugin.cloudflare.noop", reason="no urls")
            return {"plugin": self.name, "success": True, "mode": mode, "noop": True}

        use_full = mode == "full" or bool(context.get("force_full_purge"))

        if mode == "smart" and event_type == "deleted":
            use_full = True

        api_url = CF_API.format(zone_id=zone_id)
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

        if use_full:
            body = {"purge_everything": True}
            urls_purged = 0
        else:
            page_path = context.get("route", "").strip("/")
            page_url = f"{base_url}/{page_path}/" if page_path else f"{base_url}/"
            files = [page_url] + [f"{base_url}{r}" for r in related_urls]
            files = list(dict.fromkeys(files))[:30]
            body = {"files": files}
            urls_purged = len(files)

        start = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.post(api_url, headers=headers, json=body)
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPStatusError as e:
            logger.error("plugin.cloudflare.purge_failed",
                         status=e.response.status_code, body=e.response.text[:200])
            return {"plugin": self.name, "success": False,
                    "error": f"HTTP {e.response.status_code}"}
        except Exception as e:
            logger.error("plugin.cloudflare.purge_error", error=str(e))
            return {"plugin": self.name, "success": False, "error": str(e)}

        duration_ms = int((time.monotonic() - start) * 1000)
        success = data.get("success", False)

        logger.info(
            "plugin.cloudflare.purge",
            mode="full" if use_full else "partial",
            urls_purged=urls_purged,
            http_status=resp.status_code,
            duration_ms=duration_ms,
            success=success,
            trigger=f"mcp.{event_type}_page",
            route=context.get("route", ""),
        )

        return {
            "plugin": self.name,
            "success": success,
            "mode": "full" if use_full else "partial",
            "urls_purged": urls_purged,
            "duration_ms": duration_ms,
            "cf_response": data,
        }
