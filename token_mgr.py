#!/usr/bin/env python3
"""token_mgr.py — Manage hugo-mcp API tokens (bcrypt, rotation, revocation)"""

import json, os, secrets, sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

try:
    import bcrypt
except ImportError:
    print("ERROR: bcrypt not installed — run: pip install bcrypt", file=sys.stderr)
    sys.exit(1)

TOKENS_FILE = Path(__file__).parent / "tokens.json"

def _load() -> dict:
    if TOKENS_FILE.exists():
        return json.loads(TOKENS_FILE.read_text())
    return {"tokens": []}

def _save(data: dict):
    TOKENS_FILE.write_text(json.dumps(data, indent=2, default=str) + "\n")

def cmd_list(args):
    data = _load()
    tokens = data.get("tokens", [])
    if not tokens:
        print("No tokens.")
        return
    now = datetime.now(timezone.utc)
    print(f"{'ID':20s}  {'Label':25s}  {'Status':8s}  {'Created':10s}  {'Expires':10s}")
    print("-" * 82)
    for t in tokens:
        if t.get("revoked"):
            status = "REVOKED"
        elif t.get("expires"):
            try:
                if datetime.fromisoformat(t["expires"]) < now:
                    status = "EXPIRED"
                else:
                    status = "active"
            except ValueError:
                status = "?"
        else:
            status = "active"
        exp = t.get("expires", "")[:10] if t.get("expires") else "never"
        print(f"{t['id']:20s}  {t.get('label',''):25s}  {status:8s}  {t['created'][:10]}  {exp}")

def cmd_add(args):
    label = args[0] if args else "default"
    days  = int(args[1]) if len(args) > 1 else 0  # 0 = no expiry

    raw_token = secrets.token_urlsafe(32)
    hashed    = bcrypt.hashpw(raw_token.encode(), bcrypt.gensalt(rounds=12)).decode()

    now = datetime.now(timezone.utc)
    entry = {
        "id":      f"tok_{now.strftime('%Y%m%d%H%M%S')}",
        "label":   label,
        "hash":    hashed,
        "created": now.isoformat(),
        "expires": (now + timedelta(days=days)).isoformat() if days > 0 else None,
        "revoked": False,
    }

    data = _load()
    data["tokens"].append(entry)
    _save(data)

    print(f"Token created: {entry['id']}  label={label}  expires={'never' if not days else entry['expires'][:10]}")
    print(f"\n*** Copy this token — it will NOT be shown again ***\n")
    print(f"  {raw_token}\n")

def cmd_revoke(args):
    if not args:
        print("Usage: token_mgr.py revoke <token_id>")
        sys.exit(1)
    tok_id = args[0]
    data   = _load()
    for t in data["tokens"]:
        if t["id"] == tok_id:
            t["revoked"] = True
            _save(data)
            print(f"Token {tok_id} revoked.")
            return
    print(f"Token {tok_id} not found.")
    sys.exit(1)

def cmd_migrate(args):
    """Migrate existing MCP_TOKEN env var → bcrypt hash in tokens.json."""
    env_path = Path(__file__).parent / ".env"
    raw_token = os.environ.get("MCP_TOKEN", "")
    if not raw_token and env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith("MCP_TOKEN="):
                raw_token = line.split("=", 1)[1].strip().strip('"').strip("'")
                break
    if not raw_token:
        print("MCP_TOKEN not found in environment or .env")
        sys.exit(1)

    hashed = bcrypt.hashpw(raw_token.encode(), bcrypt.gensalt(rounds=12)).decode()
    now    = datetime.now(timezone.utc)
    entry  = {
        "id":      "tok_migrated",
        "label":   "migrated-from-env",
        "hash":    hashed,
        "created": now.isoformat(),
        "expires": None,
        "revoked": False,
    }

    data = _load()
    # Don't add duplicate
    if any(t["id"] == "tok_migrated" for t in data["tokens"]):
        print("tok_migrated already exists — skipping. Use 'revoke' + 'add' for rotation.")
        return
    data["tokens"].append(entry)
    _save(data)

    print("MCP_TOKEN migrated to tokens.json as 'tok_migrated'.")
    print("Clients keep the same token value — no change needed on their side.")
    print("MCP_TOKEN env var kept as fallback; remove it from .env when ready.")

CMDS = {"list": cmd_list, "add": cmd_add, "revoke": cmd_revoke, "migrate": cmd_migrate}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in CMDS:
        print(f"Usage: token_mgr.py <{'|'.join(CMDS)}> [args...]")
        print("  list               — list all tokens")
        print("  add <label> [days] — create new token (days=0 → no expiry)")
        print("  revoke <id>        — revoke a token by ID")
        print("  migrate            — migrate MCP_TOKEN from .env to tokens.json")
        sys.exit(1)
    CMDS[sys.argv[1]](sys.argv[2:])
