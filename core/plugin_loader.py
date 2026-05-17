import asyncio
import importlib.util
import sys
import yaml
from pathlib import Path
from typing import Any
import structlog

from core.plugin_base import HugoMcpPlugin

logger = structlog.get_logger(__name__)

PLUGINS_DIR = Path(__file__).parent.parent / "plugins"
CONFIG_PATH = Path(__file__).parent.parent / "config" / "plugins.yaml"
PLUGIN_TIMEOUT_SECONDS = 10
AUDIT_TIMEOUT_SECONDS = 600


class PluginRegistry:
    def __init__(self):
        self.active_plugins: list[HugoMcpPlugin] = []
        self.config: dict[str, Any] = {}

    def load(self) -> None:
        if not CONFIG_PATH.exists():
            logger.warning("plugins.config_missing", path=str(CONFIG_PATH))
            self.config = {}
        else:
            with open(CONFIG_PATH) as f:
                self.config = yaml.safe_load(f) or {}

        if not PLUGINS_DIR.exists():
            logger.warning("plugins.directory_missing", path=str(PLUGINS_DIR))
            return

        for plugin_dir in sorted(PLUGINS_DIR.iterdir()):
            if not plugin_dir.is_dir() or plugin_dir.name.startswith("_"):
                continue
            plugin_file = plugin_dir / "plugin.py"
            if not plugin_file.exists():
                continue
            try:
                self._load_plugin(plugin_dir, plugin_file)
            except Exception as e:
                logger.error("plugins.load_failed", plugin=plugin_dir.name, error=str(e))

        logger.info(
            "plugins.loaded",
            active_count=len(self.active_plugins),
            plugins=[p.name for p in self.active_plugins],
            audit_handlers=[p.name for p in self.active_plugins if getattr(p, "handles_audit", False)],
        )

    def _load_plugin(self, plugin_dir: Path, plugin_file: Path) -> None:
        plugin_name = plugin_dir.name
        module_name = f"hugo_mcp_plugin_{plugin_name.replace('-', '_')}"
        spec = importlib.util.spec_from_file_location(module_name, plugin_file)
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)

        plugin_class = None
        for obj in vars(module).values():
            if (isinstance(obj, type)
                    and issubclass(obj, HugoMcpPlugin)
                    and obj is not HugoMcpPlugin):
                plugin_class = obj
                break

        if plugin_class is None:
            logger.warning("plugins.no_class_found", plugin=plugin_name)
            return

        plugin = plugin_class()
        plugin_config = self.config.get(plugin_name.replace('-', '_'), {})

        if not plugin.is_enabled(plugin_config):
            logger.info("plugins.disabled", plugin=plugin_name)
            return

        ok, err = plugin.validate_config(plugin_config)
        if not ok:
            logger.warning("plugins.config_invalid", plugin=plugin_name, error=err)
            return

        self.active_plugins.append(plugin)
        logger.info("plugins.activated", plugin=plugin_name, version=plugin.version)

    async def fire_event(self, event_type: str, urls: list[str], context: dict) -> list[dict]:
        if not self.active_plugins:
            return []

        excluded_routes = self.config.get("excluded_routes", [])
        route = context.get("route", "")
        for excluded in excluded_routes:
            if route.startswith(excluded):
                logger.info("plugins.route_excluded", route=route)
                return []

        tasks = [
            self._call_plugin_with_timeout(p, event_type, urls, context)
            for p in self.active_plugins
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        normalized = []
        for plugin, result in zip(self.active_plugins, results):
            if isinstance(result, Exception):
                logger.error("plugins.event_failed", plugin=plugin.name, error=str(result))
                normalized.append({"plugin": plugin.name, "success": False, "error": str(result)})
            else:
                normalized.append(result)
        return normalized

    async def fire_audit_event(self, audit_type: str, context: dict) -> list[dict]:
        """
        Dispatch an audit event to plugins that opt in via ``handles_audit = True``.
        Audits are slower than page events (file IO, network calls, optional
        rebuilds), hence a separate higher timeout.
        """
        audit_plugins = [p for p in self.active_plugins if getattr(p, "handles_audit", False)]
        if not audit_plugins:
            logger.info("plugins.audit_no_handlers", audit_type=audit_type)
            return []

        tasks = [self._call_audit_with_timeout(p, audit_type, context) for p in audit_plugins]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        normalized = []
        for plugin, result in zip(audit_plugins, results):
            if isinstance(result, Exception):
                logger.error("plugins.audit_failed", plugin=plugin.name, error=str(result))
                normalized.append({"plugin": plugin.name, "success": False, "error": str(result)})
            else:
                normalized.append(result)
        return normalized

    async def _call_plugin_with_timeout(self, plugin, event_type, urls, context):
        try:
            return await asyncio.wait_for(
                plugin.on_page_event(event_type, urls, context),
                timeout=PLUGIN_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            logger.warning("plugins.timeout", plugin=plugin.name)
            return {"plugin": plugin.name, "success": False, "error": "timeout"}

    async def _call_audit_with_timeout(self, plugin, audit_type, context):
        try:
            return await asyncio.wait_for(
                plugin.on_audit(audit_type, context),
                timeout=AUDIT_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            logger.warning("plugins.audit_timeout", plugin=plugin.name)
            return {"plugin": plugin.name, "success": False, "error": "audit timeout"}


registry = PluginRegistry()
