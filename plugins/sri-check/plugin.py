"""
SRI / version audit plugin for Hugo MCP.

Wraps the standalone bash script /home/jm/scripts/check-sri-versions.sh.
Handles the ``sri_check`` audit type emitted by the ``check_sri_versions``
MCP tool. When ``auto_fix`` is requested and applied, fires a synthetic
``updated`` page event so the Cloudflare plugin performs its purge and any
other downstream plugin reacts — keeping orchestration centralized.
"""

import asyncio
import json
import os
import shlex
from pathlib import Path
from typing import Any, Literal
import structlog

from core.plugin_base import HugoMcpPlugin

logger = structlog.get_logger(__name__)


class SriCheckPlugin(HugoMcpPlugin):
    name = "sri-check"
    version = "1.0.0"
    description = "Audit SRI hashes + npm versions of CDN libs used by the Hugo site"
    requires_secret = False
    handles_audit = True

    def is_enabled(self, config: dict) -> bool:
        return config.get("enabled", False)

    def validate_config(self, config: dict) -> tuple[bool, str]:
        if not self.is_enabled(config):
            return True, ""
        script = config.get("script_path", "")
        if not script:
            return False, "Missing 'script_path' in sri_check config"
        p = Path(script)
        if not p.exists():
            return False, f"script_path does not exist: {script}"
        if not os.access(script, os.X_OK):
            return False, f"script_path is not executable: {script}"
        return True, ""

    async def on_page_event(
        self,
        event_type: Literal["created", "updated", "deleted"],
        urls: list[str],
        context: dict[str, Any],
    ) -> dict:
        # SRI check is audit-only; page events are not relevant to this plugin.
        return {"plugin": self.name, "noop": True, "reason": "audit-only plugin"}

    async def on_audit(self, audit_type: str, context: dict[str, Any]) -> dict:
        if audit_type != "sri_check":
            return {"plugin": self.name, "noop": True, "reason": f"unsupported audit_type={audit_type}"}

        from core.plugin_loader import registry  # late import to avoid cycle
        config = registry.config.get("sri_check", {})

        script_path = config["script_path"]
        auto_fix = bool(context.get("auto_fix", False))
        dry_run = bool(context.get("dry_run", False))
        trigger_cf = bool(config.get("trigger_cf_purge_on_fix", True))

        cmd = [script_path, "--json"]
        if not auto_fix:
            # Diagnostic only — disable rebuild/deploy/purge regardless of WARN.
            cmd.append("--no-autofix")
        else:
            # Auto-fix permitted, but skip the script's own CF purge if we
            # plan to orchestrate it via fire_event() instead.
            if trigger_cf:
                cmd.append("--no-cf-purge")
        if dry_run:
            cmd.append("--dry-run")

        logger.info("sri.audit.start", cmd=" ".join(shlex.quote(c) for c in cmd))

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=540)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            logger.error("sri.audit.script_timeout")
            return {"plugin": self.name, "success": False, "error": "script timeout"}

        rc = proc.returncode
        # The script prints human-readable lines plus a final JSON block.
        # We extract the last JSON object on stdout.
        report = _extract_trailing_json(stdout.decode("utf-8", errors="replace"))
        if report is None:
            logger.error("sri.audit.no_json", rc=rc, stderr=stderr[:500])
            return {
                "plugin": self.name,
                "success": False,
                "error": "could not parse JSON report from script",
                "exit_code": rc,
            }

        # If auto-fix actually applied changes, trigger the rest of the
        # orchestration through fire_event so CF + IndexNow plugins react.
        cf_orchestration = None
        applied = report.get("auto_fix", {}).get("applied") or []
        if auto_fix and trigger_cf and applied:
            base_url = registry.config.get("cloudflare", {}).get("base_url", "")
            urls = [f"{base_url}/"] if base_url else []
            cf_orchestration = await registry.fire_event(
                "updated",
                urls,
                {
                    "route": "/",
                    "lang": "fr",
                    "force_full_purge": True,
                    "trigger": "sri-check.autofix",
                },
            )
            logger.info("sri.audit.cf_orchestrated", applied=applied,
                        results=[r.get("plugin") for r in cf_orchestration])

        return {
            "plugin": self.name,
            "success": rc == 0,
            "exit_code": rc,
            "auto_fix_requested": auto_fix,
            "dry_run": dry_run,
            "report": report,
            "downstream": cf_orchestration,
        }


def _extract_trailing_json(text: str):
    """Find the last balanced JSON object in `text` and parse it.

    The bash script appends a final JSON block after human-readable log lines.
    We scan backwards for a top-level ``{ ... }`` to be robust to log content
    that contains braces or partial JSON in earlier lines.
    """
    end = text.rfind("}")
    if end == -1:
        return None
    depth = 0
    start = -1
    for i in range(end, -1, -1):
        c = text[i]
        if c == "}":
            depth += 1
        elif c == "{":
            depth -= 1
            if depth == 0:
                start = i
                break
    if start == -1:
        return None
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
