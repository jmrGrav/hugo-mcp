import json
import time
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal, Any
import httpx
import structlog

from core.plugin_base import HugoMcpPlugin

logger = structlog.get_logger(__name__)

INDEXING_API_URL = "https://indexing.googleapis.com/v3/urlNotifications:publish"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/indexing"
QUOTA_FILE = Path("/var/lib/hugo-mcp/google-indexing-quota.json")


class GoogleIndexingPlugin(HugoMcpPlugin):
    name = "google-indexing"
    version = "1.0.0"
    description = "Submit URLs to Google via Indexing API v3"
    requires_secret = True

    def __init__(self):
        self._cached_token = None
        self._cached_token_expires = None

    def is_enabled(self, config: dict) -> bool:
        return config.get("enabled", False)

    def validate_config(self, config: dict) -> tuple[bool, str]:
        if not self.is_enabled(config):
            return True, ""
        sa_path = config.get("service_account_path")
        if not sa_path:
            return False, "Missing service_account_path"
        if not Path(sa_path).exists():
            return False, f"Service account file not found: {sa_path}"
        try:
            with open(sa_path) as f:
                sa = json.load(f)
            if "client_email" not in sa or "private_key" not in sa:
                return False, "Invalid service account JSON"
        except Exception as e:
            return False, f"Cannot read service account: {e}"
        return True, ""

    def _get_quota_state(self) -> dict:
        QUOTA_FILE.parent.mkdir(parents=True, exist_ok=True)
        if not QUOTA_FILE.exists():
            return {"date": "", "used": 0}
        try:
            return json.loads(QUOTA_FILE.read_text())
        except Exception:
            return {"date": "", "used": 0}

    def _save_quota_state(self, state: dict) -> None:
        QUOTA_FILE.write_text(json.dumps(state))

    def _check_and_update_quota(self, n: int, daily_limit: int) -> tuple[bool, dict]:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        state = self._get_quota_state()
        if state.get("date") != today:
            state = {"date": today, "used": 0}
        if state["used"] + n > daily_limit:
            return False, state
        state["used"] += n
        self._save_quota_state(state)
        return True, state

    async def _get_access_token(self, sa_path: str) -> str:
        now = datetime.now(timezone.utc)
        if (self._cached_token
                and self._cached_token_expires
                and now < self._cached_token_expires - timedelta(minutes=5)):
            return self._cached_token

        try:
            import jwt
        except ImportError:
            raise RuntimeError("PyJWT not installed — run: pip install 'PyJWT[crypto]'")

        with open(sa_path) as f:
            sa = json.load(f)

        claims = {
            "iss": sa["client_email"],
            "scope": SCOPE,
            "aud": TOKEN_URL,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(hours=1)).timestamp()),
        }
        signed_jwt = jwt.encode(claims, sa["private_key"], algorithm="RS256")

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                TOKEN_URL,
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                    "assertion": signed_jwt,
                },
            )
            response.raise_for_status()
            token_data = response.json()

        self._cached_token = token_data["access_token"]
        self._cached_token_expires = now + timedelta(seconds=token_data["expires_in"])
        return self._cached_token

    async def on_page_event(
        self,
        event_type: Literal["created", "updated", "deleted"],
        urls: list[str],
        context: dict[str, Any],
    ) -> dict:
        from core.plugin_loader import registry
        config = registry.config.get("google_indexing", {})

        sa_path = config["service_account_path"]
        daily_limit = config.get("daily_quota_limit", 180)

        can_submit, quota_state = self._check_and_update_quota(len(urls), daily_limit)
        if not can_submit:
            logger.warning("plugin.google_indexing.quota_exceeded",
                           used=quota_state["used"], limit=daily_limit)
            return {
                "plugin": self.name,
                "success": False,
                "error": "daily_quota_exceeded",
                "quota_used": quota_state["used"],
                "quota_limit": daily_limit,
            }

        google_type = "URL_DELETED" if event_type == "deleted" else "URL_UPDATED"

        try:
            access_token = await self._get_access_token(sa_path)
        except Exception as e:
            logger.error("plugin.google_indexing.auth_failed", error=str(e))
            return {"plugin": self.name, "success": False, "error": f"auth_failed: {e}"}

        start = time.monotonic()
        responses = []
        async with httpx.AsyncClient(timeout=8.0) as client:
            for url in urls:
                try:
                    resp = await client.post(
                        INDEXING_API_URL,
                        headers={
                            "Authorization": f"Bearer {access_token}",
                            "Content-Type": "application/json",
                        },
                        json={"url": url, "type": google_type},
                    )
                    responses.append({"url": url, "http_status": resp.status_code, "ok": resp.status_code == 200})
                except Exception as e:
                    responses.append({"url": url, "http_status": 0, "ok": False, "error": str(e)})

        duration_ms = int((time.monotonic() - start) * 1000)
        all_ok = all(r["ok"] for r in responses)

        logger.info("plugin.google_indexing.submission",
                    event_type=event_type, urls_count=len(urls),
                    all_ok=all_ok, duration_ms=duration_ms,
                    quota_used=quota_state["used"],
                    quota_remaining=daily_limit - quota_state["used"])

        return {
            "plugin": self.name,
            "success": all_ok,
            "indexer": "google_indexing",
            "urls_submitted": len(urls),
            "responses": responses,
            "duration_ms": duration_ms,
            "quota_used_today": quota_state["used"],
            "quota_remaining_today": daily_limit - quota_state["used"],
        }
