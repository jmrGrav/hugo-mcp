#!/usr/bin/env python3
"""
Hugo MCP Server — FastAPI
Gère les pages Hugo depuis Claude.ai
"""

from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field, field_validator, ValidationError
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
import re, hmac, time, subprocess, os, yaml, json, logging, traceback, base64
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional
import httpx
import structlog
import bcrypt
from dotenv import load_dotenv
import sys, os as _os
sys.path.insert(0, _os.path.dirname(__file__))
from core.plugin_loader import registry as _plugin_registry

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("hugo-mcp")

structlog.configure(
    processors=[
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
    cache_logger_on_first_use=True,
)
slog = structlog.get_logger()

START_TIME = time.time()
METRICS    = {"create_page": 0, "update_page": 0, "delete_page": 0, "errors": 0}

ALLOWED_MONITOR_IPS          = {"127.0.0.1", "192.168.122.1"}
SENSITIVE_FRONTMATTER_FIELDS = {"aliases", "cascade", "build", "outputs", "headless", "_target"}
RESERVED_DEDICATED_PARAMS    = {"title", "tags", "draft"}
IMMUTABLE_UPDATE_FIELDS      = {"date"}
MAX_FRONTMATTER_BYTES        = 10 * 1024
MAX_FRONTMATTER_DEPTH        = 3

load_dotenv()
_plugin_registry.load()

# ── FastAPI app ───────────────────────────────────────────────────────────────

# C8: disable interactive docs — no /docs, /redoc, /openapi.json
app = FastAPI(
    title="Hugo MCP Server",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

# C1: rate limiting — 60 req/min per client IP (read X-Real-IP set by nginx)
def _get_client_ip(request: Request) -> str:
    return request.headers.get("X-Real-IP") or (request.client.host if request.client else "unknown")

limiter = Limiter(key_func=_get_client_ip)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# C8: generic exception handler — never leak tracebacks to clients
@app.exception_handler(Exception)
async def _generic_exc_handler(request: Request, exc: Exception):
    log.error("unhandled_exception path=%s error=%s\n%s",
              request.url.path, exc, traceback.format_exc())
    return JSONResponse({"error": "Internal server error"}, status_code=500)

MAX_REQUEST_BODY_SIZE = 10 * 1024 * 1024  # 10 MB (upload_asset)

@app.middleware("http")
async def limit_request_body(request: Request, call_next):
    cl = request.headers.get("content-length")
    if cl and int(cl) > MAX_REQUEST_BODY_SIZE:
        return JSONResponse(
            {"error": f"Request body too large (max {MAX_REQUEST_BODY_SIZE} bytes)"},
            status_code=413,
        )
    return await call_next(request)

HUGO_SITE             = "/home/jm/hugo-site"
CONTENT_DIR           = f"{HUGO_SITE}/content"
_CONTENT_DIR_RESOLVED = Path(CONTENT_DIR).resolve()
LANG_REGEX            = re.compile(r'^[a-z]{2,3}$')
DEPLOY_SH             = "/home/jm/deploy.sh"
MCP_TOKEN             = os.environ.get("MCP_TOKEN", "")
TOKENS_FILE           = Path(__file__).parent / "tokens.json"

CF_TOKEN    = os.environ.get("CF_TOKEN", "")
CF_ZONE_ID  = os.environ.get("CF_ZONE_ID", "")
CF_BASE_URL = "https://www.arleo.eu"

STATIC_DIR                = f"{HUGO_SITE}/static"
ASSET_EXTENSIONS_IMAGE    = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.svg', '.avif'}
ASSET_EXTENSIONS_DOCUMENT = {'.pdf', '.txt', '.csv', '.zip'}
MAX_ASSETS_RESULT         = 500
UPLOAD_ASSET_EXTENSIONS   = {".png", ".jpg", ".jpeg", ".svg", ".webp", ".gif"}
MAX_UPLOAD_BYTES          = 10 * 1024 * 1024  # 10 MB decoded
SKILL_ARLEO_IMAGE         = "/home/jm/.claude/skills/arleo-image/arleo_image.py"

# ── C4: Pydantic input models ─────────────────────────────────────────────────

def _check_tags(v: list | None) -> list | None:
    if v is None:
        return v
    if len(v) > 50:
        raise ValueError("too many tags (max 50)")
    for tag in v:
        if not isinstance(tag, str) or len(tag) > 100:
            raise ValueError("each tag must be a string ≤ 100 chars")
    return v

class CreatePageArgs(BaseModel):
    route:        str              = Field(..., min_length=1, max_length=500)
    lang:         str              = Field("fr", pattern=r'^[a-z]{2,3}$')
    title:        str              = Field("", max_length=500)
    content:      str              = Field("", max_length=524288)
    tags:         list[str]        = Field(default_factory=list)
    draft:        Optional[bool]   = None
    frontmatter:  Optional[dict | str] = None

    @field_validator("tags")
    @classmethod
    def _tags(cls, v): return _check_tags(v)

class UpdatePageArgs(BaseModel):
    route:        str              = Field(..., min_length=1, max_length=500)
    lang:         Optional[str]    = Field(None, pattern=r'^[a-z]{2,3}$')
    title:        Optional[str]    = Field(None, max_length=500)
    content:      Optional[str]    = Field(None, max_length=524288)
    tags:         Optional[list[str]] = None
    draft:        Optional[bool]   = None
    frontmatter:  Optional[dict | str] = None

    @field_validator("tags")
    @classmethod
    def _tags(cls, v): return _check_tags(v)


class UploadAssetArgs(BaseModel):
    filename:  str = Field(..., min_length=1, max_length=255)
    data:      str = Field(..., description="base64-encoded file content")
    subfolder: str = Field("images", max_length=100)

class GenerateFeaturedImageArgs(BaseModel):
    style:    str            = Field("tech")
    title:    str            = Field(..., min_length=1, max_length=80)
    subtitle: str            = Field("", max_length=120)
    tags:     list[str]      = Field(default_factory=list)
    accent:   str            = Field("")
    slug:     str            = Field(..., min_length=1, max_length=200)
    route:    Optional[str]  = Field(None, min_length=1, max_length=500)
    lang:     Optional[str]  = Field(None, pattern=r'^[a-z]{2,3}$')

    @field_validator("tags")
    @classmethod
    def _tags(cls, v): return _check_tags(v)


class CheckSriVersionsArgs(BaseModel):
    auto_fix: bool = Field(False, description="Apply minor/patch bumps (rebuild+deploy+CF purge orchestrated via plugins). Default False = diagnostic only.")
    dry_run:  bool = Field(False, description="Skip side effects (no incident POST, no heartbeat). Implies auto_fix=False.")

# ── Input validation ──────────────────────────────────────────────────────────

def normalize_route(route: str) -> tuple[str, bool]:
    s = route.strip('/')
    return ('_index', True) if s in ('', '_index', 'index') else (s, False)

def _safe_route(route: str) -> str:
    normalized, is_root = normalize_route(route)
    if is_root:
        return '_index'
    candidate = (_CONTENT_DIR_RESOLVED / normalized).resolve()
    try:
        candidate.relative_to(_CONTENT_DIR_RESOLVED)
    except ValueError:
        raise HTTPException(400, f"Invalid route (path traversal): {route}")
    return normalized

def _safe_lang(lang: str | None) -> str | None:
    if not lang:
        return None
    if not LANG_REGEX.match(lang):
        raise HTTPException(400, f"Invalid lang (must match ^[a-z]{{2,3}}$): {lang}")
    return lang

def _validate_frontmatter(
    fm,
    *,
    is_update: bool = False,
    dedicated_params: dict | None = None,
) -> dict:
    if fm is None:
        return {}
    if not isinstance(fm, dict):
        raise HTTPException(400, "frontmatter must be a dict (or null)")

    size = len(json.dumps(fm).encode("utf-8"))
    if size > MAX_FRONTMATTER_BYTES:
        raise HTTPException(400, f"frontmatter too large: {size} bytes (max {MAX_FRONTMATTER_BYTES})")

    forbidden = set(fm.keys()) & SENSITIVE_FRONTMATTER_FIELDS
    if forbidden:
        raise HTTPException(400,
            f"Forbidden frontmatter fields (security): {', '.join(sorted(forbidden))}")

    if dedicated_params:
        provided = {k for k, v in dedicated_params.items()
                    if v is not None and v != [] and v != ""}
        conflicts = set(fm.keys()) & provided
        if conflicts:
            raise HTTPException(400,
                f"Conflict: field(s) provided both as dedicated param and in frontmatter: "
                f"{', '.join(sorted(conflicts))}. Use only one.")

    def _check(value, depth: int, path: str) -> None:
        if depth > MAX_FRONTMATTER_DEPTH:
            raise HTTPException(400,
                f"frontmatter too deep at '{path}' (max depth {MAX_FRONTMATTER_DEPTH})")
        if value is None:
            if not is_update:
                raise HTTPException(400,
                    f"null not allowed at '{path}' on create_page "
                    f"(only valid on update_page for field deletion)")
            return
        if not isinstance(value, (str, int, float, bool, list, dict)):
            raise HTTPException(400,
                f"Invalid type at '{path}': {type(value).__name__} "
                f"(allowed: str, int, float, bool, list, dict, null)")
        if isinstance(value, dict):
            for k, v in value.items():
                if not isinstance(k, str):
                    raise HTTPException(400, f"Non-string key at '{path}': {k!r}")
                _check(v, depth + 1, f"{path}.{k}")
        elif isinstance(value, list):
            for i, item in enumerate(value):
                _check(item, depth + 1, f"{path}[{i}]")

    for k, v in fm.items():
        if not isinstance(k, str):
            raise HTTPException(400, f"Non-string top-level key: {k!r}")
        _check(v, 1, k)

    return fm


def _deep_merge(existing: dict, updates: dict) -> dict:
    result = dict(existing)
    for k, v in updates.items():
        if v is None:
            result.pop(k, None)
        elif isinstance(v, dict) and isinstance(result.get(k), dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result

# ── C2/C5: Auth — tokens.json with bcrypt, fallback to MCP_TOKEN env var ──────

def _load_tokens() -> list[dict]:
    try:
        data = json.loads(TOKENS_FILE.read_text())
        return data.get("tokens", [])
    except (OSError, json.JSONDecodeError):
        return []

def verify_token(request: Request):
    auth  = request.headers.get("Authorization", "")
    raw   = auth.replace("Bearer ", "").strip()
    if not raw:
        raise HTTPException(status_code=401, detail="Unauthorized")

    raw_bytes = raw.encode()

    # Check tokens.json (bcrypt hashes)
    now = datetime.now(timezone.utc)
    for entry in _load_tokens():
        if entry.get("revoked"):
            continue
        expires = entry.get("expires")
        if expires:
            try:
                if datetime.fromisoformat(expires) < now:
                    continue
            except ValueError:
                continue
        stored = entry.get("hash", "")
        if stored:
            try:
                if bcrypt.checkpw(raw_bytes, stored.encode()):
                    return entry.get("id", "token")
            except Exception:
                continue

    # Fallback: MCP_TOKEN env var (plain hmac, for migration / backwards compat)
    if MCP_TOKEN and hmac.compare_digest(raw_bytes, MCP_TOKEN.encode()):
        return "env_token"

    raise HTTPException(status_code=401, detail="Unauthorized")

# ── Helpers ───────────────────────────────────────────────────────────────────

def run_deploy() -> str:
    result = subprocess.run(
        ["bash", DEPLOY_SH],
        capture_output=True, text=True, timeout=120
    )
    if result.returncode != 0:
        raise HTTPException(500, f"Deploy failed: {result.stderr}")
    return result.stdout.strip()


def read_frontmatter(filepath: str):
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    if not content.startswith('---'):
        return {}, content

    parts = content.split('---', 2)
    if len(parts) < 3:
        return {}, content

    try:
        fm = yaml.safe_load(parts[1])
        if not isinstance(fm, dict):
            fm = {}
    except yaml.YAMLError as e:
        log.warning("frontmatter_invalid file=%s error=%s", filepath, e)
        fm = {}

    return fm, parts[2].strip()

def write_page(filepath: str, frontmatter: dict, content: str):
    fm_str       = yaml.safe_dump(frontmatter, allow_unicode=True, default_flow_style=False, sort_keys=False)
    full_content = f"---\n{fm_str}---\n\n{content}\n"
    Path(filepath).parent.mkdir(parents=True, exist_ok=True)
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(full_content)

def find_page(route: str, lang: str = None) -> str | None:
    if route == '_index':
        if lang:
            p = f"{CONTENT_DIR}/_index.{lang}.md"
            if Path(p).exists():
                return p
        p = f"{CONTENT_DIR}/_index.md"
        return p if Path(p).exists() else None
    route = route.strip('/')
    if lang:
        candidate = f"{CONTENT_DIR}/{route}/index.{lang}.md"
        if Path(candidate).exists():
            return candidate
    candidate = f"{CONTENT_DIR}/{route}/index.md"
    if Path(candidate).exists():
        return candidate
    return None

# ── MCP Endpoint ──────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "service": "hugo-mcp"}

@app.get("/healthz")
def healthz():
    return {"status": "ok"}

@app.get("/readyz")
def readyz():
    if not _CONTENT_DIR_RESOLVED.exists():
        return JSONResponse({"status": "not ready", "reason": "content dir missing"}, status_code=503)
    return {"status": "ready"}

@app.get("/metrics")
def metrics(request: Request):
    if request.client.host not in ALLOWED_MONITOR_IPS:
        raise HTTPException(403, "Forbidden")
    uptime = int(time.time() - START_TIME)
    lines = [
        "# TYPE hugo_mcp_uptime_seconds counter",
        f"hugo_mcp_uptime_seconds {uptime}",
    ]
    for k, v in METRICS.items():
        lines += [f"# TYPE hugo_mcp_{k}_total counter", f"hugo_mcp_{k}_total {v}"]
    return Response("\n".join(lines) + "\n", media_type="text/plain")

@app.post("/mcp")
@limiter.limit("60/minute")
async def mcp_handler(request: Request, _=Depends(verify_token)):
    body   = await request.json()
    method = body.get("method", "")
    params = body.get("params", {})
    req_id = body.get("id", 1)

    handlers = {
        "initialize": handle_initialize,
        "tools/list": handle_list_tools,
        "tools/call": handle_tool_call,
    }

    handler = handlers.get(method)
    if not handler:
        return JSONResponse({"jsonrpc": "2.0", "id": req_id,
                             "error": {"code": -32601, "message": f"Method not found: {method}"}})

    try:
        if method == "tools/call":
            result = await handle_tool_call(params, request)
        else:
            result = await handler(params)
        return JSONResponse({"jsonrpc": "2.0", "id": req_id, "result": result})
    except HTTPException as e:
        return JSONResponse({"jsonrpc": "2.0", "id": req_id,
                             "error": {"code": e.status_code, "message": e.detail}})

async def handle_initialize(params):
    return {
        "protocolVersion": "2025-03-26",
        "capabilities":    {"tools": {}},
        "serverInfo":      {"name": "hugo-mcp", "version": "1.0.0"},
    }

async def tool_generate_featured_image(args):
    try:
        v = GenerateFeaturedImageArgs.model_validate(args)
    except ValidationError as e:
        raise HTTPException(400, f"Invalid arguments: {e}")

    # Validate slug: no path traversal, only safe chars (lowercase alphanumeric + hyphens)
    if ".." in v.slug or "/" in v.slug or "\\" in v.slug:
        raise HTTPException(400, "slug must not contain path separators or ..")
    if not re.fullmatch(r"[a-z0-9][a-z0-9\-]*", v.slug):
        raise HTTPException(400, "slug must be lowercase alphanumeric + hyphens")

    filename = f"{v.slug}-featured.jpg"

    # Validate style
    if v.style not in ("tech", "geo"):
        raise HTTPException(400, f"style must be 'tech' or 'geo', got {v.style!r}")

    # Validate accent if provided
    if v.accent and not re.fullmatch(r"#[0-9a-fA-F]{6}", v.accent):
        raise HTTPException(400, f"accent must be a 6-digit hex color like #7aa2f7, got {v.accent!r}")

    outpath = f"{STATIC_DIR}/images/{filename}"

    # Run skill in isolated namespace
    ns = {}
    try:
        exec(open(SKILL_ARLEO_IMAGE).read(), ns)
        ns["generate"](v.style, v.title, v.subtitle, v.tags, v.accent or None,
                       outpath)
    except Exception as e:
        log.error("generate_featured_image skill error: %s", e)
        raise HTTPException(500, f"Skill error: {e}")

    if not os.path.exists(outpath):
        raise HTTPException(500, f"Skill ran but file not created: {outpath}")
    if os.path.getsize(outpath) == 0:
        raise HTTPException(500, f"Skill produced empty file: {outpath}")
    size_kb = os.path.getsize(outpath) // 1024

    frontmatter_updated = False
    langs_updated = []
    if v.route:
        route_safe = _safe_route(v.route)
        page_paths = []
        for lang in ("fr", "en"):
            p = find_page(route_safe, lang)
            if p:
                page_paths.append((lang, p))
        if not page_paths:
            p = find_page(route_safe, None)
            if p:
                page_paths.append((None, p))
        if not page_paths:
            raise HTTPException(404, f"Page not found: {v.route}")
        now_str = datetime.now().strftime("%Y-%m-%dT%H:%M:%S+02:00")
        for lang, page_path in page_paths:
            fm, existing_content = read_frontmatter(page_path)
            for key in ("featuredimage", "featuredImage", "FeaturedImage",
                        "featuredimagedark", "featuredImageDark", "FeaturedImageDark"):
                fm.pop(key, None)
            fm["featuredImage"] = f"/images/{filename}"
            fm["lastmod"] = now_str
            write_page(page_path, fm, existing_content)
            langs_updated.append(lang or "default")
        frontmatter_updated = True

    deploy_output = run_deploy()

    return {
        "status":              "ok",
        "filename":            filename,
        "public_url":          f"/images/{filename}",
        "size_kb":             size_kb,
        "style":               v.style,
        "frontmatter_updated": frontmatter_updated,
        "langs_updated":       langs_updated,
        "deploy":              deploy_output,
    }

async def handle_list_tools(params):
    return {"tools": [
        {
            "name":        "list_pages",
            "description": "Lister toutes les pages du site Hugo",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "lang":    {"type": "string", "description": "Langue (fr, en). Optionnel."},
                    "section": {"type": "string", "description": "Section (posts, pages...). Optionnel."},
                },
            },
        },
        {
            "name":        "get_page",
            "description": "Lire le contenu d'une page Hugo",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "route": {"type": "string", "description": "Route de la page ex: /posts/mon-article"},
                    "lang":  {"type": "string", "description": "Langue (fr, en)"},
                },
                "required": ["route"],
            },
        },
        {
            "name":        "create_page",
            "description": "Créer une nouvelle page Hugo + rebuild + purge Cloudflare ciblée",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "route":        {"type": "string", "description": "Route ex: /posts/mon-article"},
                    "lang":         {"type": "string", "description": "Langue (fr, en)", "default": "fr"},
                    "title":        {"type": "string", "description": "Titre de la page"},
                    "content":      {"type": "string", "description": "Contenu Markdown (sans front matter)"},
                    "tags":         {"type": "array", "items": {"type": "string"}},
                    "draft":        {"type": "boolean", "default": False},
                    "frontmatter":  {
                        "type": "object",
                        "additionalProperties": True,
                        "description": (
                            "Champs frontmatter libres (description, categories, featuredImage, "
                            "lastmod, date, etc.). Chaque valeur : string/number/boolean/list/dict "
                            "(max 3 niveaux, 10 KB total). "
                            "Champs interdits : aliases, cascade, build, outputs, headless, _target → HTTP 400. "
                            "Conflit avec un param dédié (title, tags, draft) → HTTP 400. "
                            "date et lastmod auto-générés si absents."
                        ),
                    },
                },
                "required": ["route", "title", "content"],
            },
        },
        {
            "name":        "update_page",
            "description": "Modifier une page existante + rebuild + purge Cloudflare ciblée",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "route":       {"type": "string"},
                    "lang":        {"type": "string"},
                    "title":       {"type": "string"},
                    "content":     {"type": "string"},
                    "tags":        {"type": "array", "items": {"type": "string"}},
                    "draft":       {"type": "boolean"},
                    "frontmatter": {
                        "type": "object",
                        "additionalProperties": True,
                        "description": (
                            "Champs frontmatter libres mergés profondément sur le frontmatter existant. "
                            "Utiliser null pour supprimer un champ (ex: {\"description\": null}). "
                            "Chaque valeur : string/number/boolean/list/dict (max 3 niveaux, 10 KB). "
                            "Champs interdits : aliases, cascade, build, outputs, headless, _target → HTTP 400. "
                            "Conflit avec un param dédié (title, tags, draft) → HTTP 400. "
                            "date est immuable. lastmod auto-mis à jour si absent."
                        ),
                    },
                },
                "required": ["route"],
            },
        },
        {
            "name":        "delete_page",
            "description": "Supprimer une page + rebuild + purge Cloudflare totale",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "route": {"type": "string"},
                    "lang":  {"type": "string"},
                },
                "required": ["route"],
            },
        },
        {
            "name":        "build_site",
            "description": "Rebuild Hugo + déploiement + purge Cloudflare totale",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "purge_cf": {"type": "boolean", "default": True},
                },
            },
        },
        {
            "name":        "upload_asset",
            "description": "Uploader une image dans static/{subfolder}/ du site Hugo",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "filename":  {"type": "string", "description": "Nom du fichier ex: schema.png"},
                    "data":      {"type": "string", "description": "Contenu encodé en base64"},
                    "subfolder": {"type": "string", "default": "images",
                                  "description": "Sous-dossier dans static/ (defaut: images)"},
                },
                "required": ["filename", "data"],
            },
        },
                {
            "name":        "list_assets",
            "description": "Lister les assets du site Hugo (static/ et page bundles dans content/), triés par date de modification décroissante",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "type":        {"type": "string", "enum": ["image", "document", "all"], "default": "all",
                                   "description": "Filtrer par type : image (jpg/png/svg…), document (pdf/csv/zip…), all"},
                    "path_prefix": {"type": "string",
                                   "description": "Sous-dossier relatif à filtrer (ex: posts/mon-article/). Sans leading /, sans .."},
                    "max_results": {"type": "integer", "default": 100, "maximum": 500,
                                   "description": "Nombre max de résultats (défaut 100, max 500)"},
                },
            },
        },
        {
            "name":        "check_sri_versions",
            "description": "Audit SRI hashes + npm versions of CDN libs used by the Hugo site. With auto_fix=true: bump minor/patch outdated libs, rebuild, deploy, orchestrate CF purge via plugins, resolve BetterStack incident.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "auto_fix": {"type": "boolean", "default": False,
                                  "description": "Apply minor/patch bumps. Major bumps and hash mismatches always require manual review."},
                    "dry_run":  {"type": "boolean", "default": False,
                                  "description": "Diagnostic only ; skip incident POST and heartbeat ping. Implies auto_fix=False."},
                },
            },
        },
        {
            "name":        "generate_featured_image",
            "description": "Générer une featured image Tokyo Night pour un article arleo.eu via le skill arleo-image (sans base64)",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "style":    {"type": "string", "enum": ["tech", "geo"], "default": "tech",
                                "description": "Style visuel : tech (infra/sécu) ou geo (général)"},
                    "title":    {"type": "string", "description": "Titre principal (max 80 chars)"},
                    "subtitle": {"type": "string", "description": "Sous-titre (max 120 chars, optionnel)"},
                    "tags":     {"type": "array", "items": {"type": "string"},
                                "description": "Tags affichés sur l'image"},
                    "accent":   {"type": "string",
                                "description": "Couleur accent hex optionnelle (#7aa2f7, #9ece6a, #f7768e, #e0af68, #bb9af7, #7dcfff)"},
                    "slug":     {"type": "string",
                                "description": "Slug pour le filename de sortie (lowercase alphanumeric+hyphens). Le fichier final est {slug}-featured.jpg"},
                    "route":    {"type": "string",
                                "description": "Route de la page à mettre à jour (ex: /posts/mon-article). Si fourni, met à jour featuredImage dans le frontmatter."},
                    "lang":     {"type": "string",
                                "description": "Langue de la page (fr, en). Défaut: fr."},
                },
                "required": ["title", "slug"],
            },
        },
    ]}

async def handle_tool_call(params, request=None):
    tool_name = params.get("name", "")
    args      = params.get("arguments", {})

    _WRITE_TOOLS = {"create_page", "update_page", "delete_page"}
    if tool_name in _WRITE_TOOLS:
        ip = _get_client_ip(request) if request else "unknown"
        slog.info("write_op", op=tool_name, route=args.get("route", "-"), lang=args.get("lang", "-"), ip=ip)
        METRICS[tool_name] = METRICS.get(tool_name, 0) + 1

    tools = {
        "list_pages":  tool_list_pages,
        "get_page":    tool_get_page,
        "create_page": tool_create_page,
        "update_page": tool_update_page,
        "delete_page": tool_delete_page,
        "build_site":  tool_build_site,
        "list_assets": tool_list_assets,
        "upload_asset": tool_upload_asset,
        "generate_featured_image": tool_generate_featured_image,
        "check_sri_versions":      tool_check_sri_versions,
    }

    tool = tools.get(tool_name)
    if not tool:
        raise HTTPException(404, f"Tool not found: {tool_name}")
    try:
        result = await tool(args)
        return {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]}
    except HTTPException as e:
        log.warning("tool_error tool=%s http=%s detail=%s", tool_name, e.status_code, e.detail)
        METRICS["errors"] = METRICS.get("errors", 0) + 1
        return {"content": [{"type": "text", "text": e.detail}], "isError": True}
    except Exception as e:
        log.error("tool_error tool=%s error=%s\n%s", tool_name, e, traceback.format_exc())
        METRICS["errors"] = METRICS.get("errors", 0) + 1
        return {"content": [{"type": "text", "text": f"{type(e).__name__}: {e}"}], "isError": True}

# ── Tools ─────────────────────────────────────────────────────────────────────

async def tool_list_pages(args):
    lang    = args.get("lang")
    section = args.get("section")
    pages   = []
    skipped = 0

    scan_path = Path(CONTENT_DIR)
    if section:
        scan_path = scan_path / section.strip('/')
    if not scan_path.exists():
        log.warning("list_pages: path absent: %s", scan_path)
        return {"pages": [], "total": 0}

    try:
        md_files = list(scan_path.glob("_index.*.md")) + list(scan_path.rglob("index.*.md"))
    except (PermissionError, OSError) as e:
        log.error("list_pages: walk failed: %s", e)
        return {"pages": [], "total": 0, "error": str(e)}

    for path in md_files:
        stem_parts = path.stem.split('.')
        if len(stem_parts) != 2 or stem_parts[0] not in ('index', '_index'):
            continue
        file_lang = stem_parts[1]

        if lang and file_lang != lang:
            continue

        try:
            fm, _ = read_frontmatter(str(path))
        except (OSError, PermissionError) as e:
            log.warning("list_pages: skip %s: %s", path, e)
            skipped += 1
            continue

        if path.name.startswith('_index.') and path.parent == Path(CONTENT_DIR):
            route = '/'
        else:
            route = '/' + str(path.parent.relative_to(CONTENT_DIR))

        pages.append({
            "route": route,
            "lang":  file_lang,
            "file":  str(path).replace(CONTENT_DIR + '/', ''),
            "title": fm.get("title", ""),
            "date":  str(fm.get("date", "")),
            "draft": fm.get("draft", False),
            "tags":  fm.get("tags", []),
        })

    pages.sort(key=lambda x: x.get("date", ""), reverse=True)
    result = {"pages": pages, "total": len(pages)}
    if skipped:
        result["skipped"] = skipped
        log.warning("list_pages: %d file(s) skipped due to read errors", skipped)
    return result

async def tool_get_page(args):
    route    = _safe_route(args.get("route", ""))
    lang     = _safe_lang(args.get("lang"))
    filepath = find_page(route, lang)

    if not filepath:
        raise HTTPException(404, f"Page not found: {route} (lang={lang})")

    fm, content = read_frontmatter(filepath)
    return {
        "route":       "/" if route == '_index' else route,
        "file":        filepath.replace(CONTENT_DIR + '/', ''),
        "frontmatter": fm,
        "content":     content,
    }

async def tool_create_page(args):
    # C4: Pydantic validation
    try:
        v = CreatePageArgs.model_validate(args)
    except ValidationError as e:
        raise HTTPException(400, f"Invalid arguments: {e}")

    route   = _safe_route(v.route)
    lang    = _safe_lang(v.lang) or "fr"
    title   = v.title
    content = v.content
    tags    = v.tags or None
    draft   = v.draft
    fm_user = v.frontmatter

    if isinstance(fm_user, str):
        try:
            fm_user = json.loads(fm_user)
        except json.JSONDecodeError as e:
            log.warning("frontmatter invalid JSON string: %s", e)
            fm_user = None

    fm_user = _validate_frontmatter(
        fm_user,
        is_update=False,
        dedicated_params={"title": title, "tags": tags, "draft": draft},
    )

    filepath = (f"{CONTENT_DIR}/_index.{lang}.md" if route == '_index'
                else f"{CONTENT_DIR}/{route}/index.{lang}.md")
    if Path(filepath).exists():
        raise HTTPException(409, f"Page already exists: {filepath}")

    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S+02:00")
    final_fm: dict = {"title": title}
    if tags is not None:
        final_fm["tags"] = tags
    if draft is not None:
        final_fm["draft"] = draft
    if "date" not in fm_user:
        final_fm["date"] = now
    if "lastmod" not in fm_user:
        final_fm["lastmod"] = now
    final_fm.update(fm_user)

    t0 = time.perf_counter()
    write_page(filepath, final_fm, content)
    t_write = time.perf_counter()
    deploy_output = run_deploy()
    t_build = time.perf_counter()
    t_purge = time.perf_counter()
    slog.info("timing", op="create_page", route=route, lang=lang,
              write_ms=int((t_write-t0)*1000), build_ms=int((t_build-t_write)*1000),
              purge_ms=int((t_purge-t_build)*1000), total_ms=int((t_purge-t0)*1000))

    _urls = [f"{CF_BASE_URL}{route if route.startswith('/') else '/' + route}/"]
    _plugin_results = await _plugin_registry.fire_event("created", _urls, {"route": route, "lang": lang, "title": title})
    cf_result = next((r for r in _plugin_results if r.get("plugin") == "cloudflare"), {"skipped": "plugin not active"})
    return {
        "status":   "created",
        "file":     filepath.replace(CONTENT_DIR + '/', ''),
        "deploy":   deploy_output,
        "cf_purge": cf_result,
        "plugins":  _plugin_results,
    }

async def tool_update_page(args):
    # C4: Pydantic validation
    try:
        v = UpdatePageArgs.model_validate(args)
    except ValidationError as e:
        raise HTTPException(400, f"Invalid arguments: {e}")

    route   = _safe_route(v.route)
    lang    = _safe_lang(v.lang)
    content = v.content
    fm_user = v.frontmatter

    if isinstance(fm_user, str):
        try:
            fm_user = json.loads(fm_user)
        except json.JSONDecodeError as e:
            log.warning("frontmatter invalid JSON string: %s", e)
            fm_user = None

    fm_user = _validate_frontmatter(
        fm_user,
        is_update=True,
        dedicated_params={
            "title": v.title,
            "tags":  v.tags,
            "draft": v.draft,
        },
    )

    immutable_attempts = set(fm_user.keys()) & IMMUTABLE_UPDATE_FIELDS
    if immutable_attempts:
        raise HTTPException(400,
            f"Field(s) cannot be modified via update_page: "
            f"{', '.join(sorted(immutable_attempts))}")

    filepath = find_page(route, lang)
    if not filepath:
        raise HTTPException(404, f"Page not found: {route}")

    fm, existing_content = read_frontmatter(filepath)

    final_content = content if content is not None else existing_content

    final_fm = dict(fm)
    if v.title is not None:
        final_fm["title"] = v.title
    if v.tags is not None:
        final_fm["tags"] = v.tags
    if v.draft is not None:
        final_fm["draft"] = v.draft
    if "lastmod" not in fm_user:
        final_fm["lastmod"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S+02:00")

    final_fm = _deep_merge(final_fm, fm_user)

    t0 = time.perf_counter()
    write_page(filepath, final_fm, final_content)
    t_write = time.perf_counter()
    deploy_output = run_deploy()
    t_build = time.perf_counter()
    t_purge = time.perf_counter()
    slog.info("timing", op="update_page", route=route, lang=lang,
              write_ms=int((t_write-t0)*1000), build_ms=int((t_build-t_write)*1000),
              purge_ms=int((t_purge-t_build)*1000), total_ms=int((t_purge-t0)*1000))

    _urls = [f"{CF_BASE_URL}{route if route.startswith('/') else '/' + route}/"]
    _plugin_results = await _plugin_registry.fire_event("updated", _urls, {"route": route, "lang": lang})
    cf_result = next((r for r in _plugin_results if r.get("plugin") == "cloudflare"), {"skipped": "plugin not active"})
    return {
        "status":   "updated",
        "file":     filepath.replace(CONTENT_DIR + '/', ''),
        "deploy":   deploy_output,
        "cf_purge": cf_result,
        "plugins":  _plugin_results,
    }

async def tool_delete_page(args):
    route    = _safe_route(args.get("route", ""))
    lang     = _safe_lang(args.get("lang"))
    filepath = find_page(route, lang)

    if not filepath:
        raise HTTPException(404, f"Page not found: {route}")

    t0 = time.perf_counter()
    os.remove(filepath)
    parent = Path(filepath).parent
    if parent.exists() and not any(parent.iterdir()):
        parent.rmdir()
    t_write = time.perf_counter()
    deploy_output = run_deploy()
    t_build = time.perf_counter()
    t_purge = time.perf_counter()
    slog.info("timing", op="delete_page", route=route, lang=lang,
              delete_ms=int((t_write-t0)*1000), build_ms=int((t_build-t_write)*1000),
              purge_ms=int((t_purge-t_build)*1000), total_ms=int((t_purge-t0)*1000))

    _urls = [f"{CF_BASE_URL}{route if route.startswith('/') else '/' + route}/"]
    _plugin_results = await _plugin_registry.fire_event("deleted", _urls, {"route": route, "lang": lang})
    cf_result = next((r for r in _plugin_results if r.get("plugin") == "cloudflare"), {"skipped": "plugin not active"})
    return {
        "status":   "deleted",
        "file":     filepath.replace(CONTENT_DIR + '/', ''),
        "deploy":   deploy_output,
        "cf_purge": cf_result,
        "plugins":  _plugin_results,
    }

async def tool_build_site(args):
    purge_cf      = args.get("purge_cf", True)
    t0 = time.perf_counter()
    deploy_output = run_deploy()
    t_build = time.perf_counter()
    cf_result     = {"skipped": "use cloudflare plugin"}
    t_purge = time.perf_counter()
    slog.info("timing", op="build_site",
              build_ms=int((t_build-t0)*1000), purge_ms=int((t_purge-t_build)*1000),
              total_ms=int((t_purge-t0)*1000))

    return {"status": "built", "deploy": deploy_output, "cf_purge": cf_result}


async def tool_check_sri_versions(args):
    try:
        v = CheckSriVersionsArgs.model_validate(args)
    except ValidationError as e:
        raise HTTPException(400, f"Invalid arguments: {e}")

    auto_fix = v.auto_fix and not v.dry_run
    context = {"auto_fix": auto_fix, "dry_run": v.dry_run}

    t0 = time.perf_counter()
    results = await _plugin_registry.fire_audit_event("sri_check", context)
    duration_ms = int((time.perf_counter() - t0) * 1000)

    slog.info("timing", op="check_sri_versions",
              total_ms=duration_ms, auto_fix=auto_fix, dry_run=v.dry_run,
              handlers=len(results))

    if not results:
        return {
            "status": "no_handlers",
            "message": "No plugin registered for audit_type=sri_check (check plugins.yaml).",
            "duration_ms": duration_ms,
        }

    return {
        "status": "ok",
        "auto_fix": auto_fix,
        "dry_run": v.dry_run,
        "duration_ms": duration_ms,
        "results": results,
    }


async def tool_upload_asset(args):
    try:
        v = UploadAssetArgs.model_validate(args)
    except ValidationError as e:
        raise HTTPException(400, f"Invalid arguments: {e}")

    # Validate filename — no path separators or traversal
    if "/" in v.filename or "\\" in v.filename or ".." in v.filename:
        raise HTTPException(400, "Invalid filename: must not contain /, backslash, or ..")

    suffix = Path(v.filename).suffix.lower()
    if suffix not in UPLOAD_ASSET_EXTENSIONS:
        raise HTTPException(400, f"Unsupported extension {suffix!r}. Allowed: {sorted(UPLOAD_ASSET_EXTENSIONS)}")

    # Validate subfolder
    if ".." in v.subfolder or v.subfolder.startswith("/"):
        raise HTTPException(400, "Invalid subfolder: must be relative without ..")

    # Decode base64
    try:
        file_bytes = base64.b64decode(v.data, validate=True)
    except Exception:
        raise HTTPException(400, "Invalid base64 data")

    if len(file_bytes) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"File too large: {len(file_bytes)} bytes (max {MAX_UPLOAD_BYTES})")

    # Build destination — prevent path traversal
    static_root = Path(STATIC_DIR).resolve()
    dest_dir    = (static_root / v.subfolder).resolve()
    try:
        dest_dir.relative_to(static_root)
    except ValueError:
        raise HTTPException(400, "Invalid subfolder: path traversal detected")

    dest_path = (dest_dir / v.filename).resolve()
    try:
        dest_path.relative_to(static_root)
    except ValueError:
        raise HTTPException(400, "Invalid filename: path traversal detected")

    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path.write_bytes(file_bytes)

    deploy_output = run_deploy()
    slog.info("upload_asset", filename=v.filename, subfolder=v.subfolder, size=len(file_bytes))

    return {
        "status":     "ok",
        "path":       str(dest_path.relative_to(Path(HUGO_SITE))),
        "public_url": f"/{v.subfolder}/{v.filename}",
        "size_bytes": len(file_bytes),
        "deploy":     deploy_output,
    }


async def tool_list_assets(args):
    asset_type  = args.get("type", "all")
    path_prefix = args.get("path_prefix", "")
    max_results = min(int(args.get("max_results", 100)), MAX_ASSETS_RESULT)

    if path_prefix and (path_prefix.startswith('/') or '..' in path_prefix):
        raise HTTPException(400, "Invalid path_prefix: must be relative without '..'")

    exts = (ASSET_EXTENSIONS_IMAGE    if asset_type == "image"
            else ASSET_EXTENSIONS_DOCUMENT if asset_type == "document"
            else ASSET_EXTENSIONS_IMAGE | ASSET_EXTENSIONS_DOCUMENT)

    _MIME = {'.jpg':'image/jpeg','.jpeg':'image/jpeg','.png':'image/png',
             '.gif':'image/gif','.webp':'image/webp','.svg':'image/svg+xml',
             '.avif':'image/avif','.pdf':'application/pdf','.txt':'text/plain',
             '.csv':'text/csv','.zip':'application/zip'}

    assets = []
    for scan_root in (Path(STATIC_DIR), Path(CONTENT_DIR)):
        if not scan_root.exists():
            continue
        base = (scan_root / path_prefix) if path_prefix else scan_root
        try:
            base.resolve().relative_to(scan_root.resolve())
        except ValueError:
            raise HTTPException(400, "Invalid path_prefix: path traversal detected")
        if not base.exists():
            continue
        for p in base.rglob("*"):
            if not p.is_file() or p.suffix.lower() not in exts:
                continue
            if p.name.startswith(('index.', '_index.')):
                continue
            try:
                st = p.stat()
                assets.append({
                    "path":       str(p.relative_to(Path(HUGO_SITE))),
                    "size_bytes": st.st_size,
                    "mime_type":  _MIME.get(p.suffix.lower(), "application/octet-stream"),
                    "modified":   datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%dT%H:%M:%S"),
                })
            except OSError:
                continue

    assets.sort(key=lambda a: a["modified"], reverse=True)
    truncated = len(assets) > max_results
    return {"count": min(len(assets), max_results), "truncated": truncated, "assets": assets[:max_results]}

# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
