#!/usr/bin/env python3
"""
Hugo MCP Server — FastAPI
Gère les pages Hugo depuis Claude.ai
"""

from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.responses import JSONResponse
import subprocess, os, glob, yaml, json, logging, traceback
from pathlib import Path
from datetime import datetime
import httpx
from dotenv import load_dotenv

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("hugo-mcp")

load_dotenv()

app = FastAPI(title="Hugo MCP Server")

HUGO_SITE   = "/home/jm/hugo-site"
CONTENT_DIR = f"{HUGO_SITE}/content"
DEPLOY_SH   = "/home/jm/deploy.sh"
MCP_TOKEN   = os.environ.get("MCP_TOKEN", "")

CF_TOKEN    = os.environ.get("CF_TOKEN", "")
CF_ZONE_ID  = os.environ.get("CF_ZONE_ID", "")
CF_BASE_URL = "https://hugo-test.arleo.eu"

# ── Auth ──────────────────────────────────────────────────────────────────────

def verify_token(request: Request):
    auth  = request.headers.get("Authorization", "")
    token = auth.replace("Bearer ", "").strip()
    if not token or token != MCP_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return token

# ── Helpers ───────────────────────────────────────────────────────────────────

def run_deploy() -> str:
    result = subprocess.run(
        ["bash", DEPLOY_SH],
        capture_output=True, text=True, timeout=120
    )
    if result.returncode != 0:
        raise HTTPException(500, f"Deploy failed: {result.stderr}")
    return result.stdout.strip()

async def purge_cloudflare(paths: list[str] = None):
    if not CF_TOKEN or not CF_ZONE_ID:
        return {"skipped": "CF credentials not configured"}

    url     = f"https://api.cloudflare.com/client/v4/zones/{CF_ZONE_ID}/purge_cache"
    headers = {"Authorization": f"Bearer {CF_TOKEN}", "Content-Type": "application/json"}
    body    = {"files": [f"{CF_BASE_URL}{p}" for p in paths]} if paths else {"purge_everything": True}

    async with httpx.AsyncClient() as client:
        resp = await client.post(url, headers=headers, json=body)
        return resp.json()

def read_frontmatter(filepath: str):
    # OSError / PermissionError propagate intentionally — callers skip the file
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

@app.post("/mcp")
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
                    "frontmatter":  {"type": "object", "description": "Champs YAML libres (description, url, categories, featuredImage, toc, date custom…). Mergé en base ; title/tags/draft explicites priment en cas de conflit."},
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
                    "frontmatter": {"type": "object", "description": "Champs YAML libres mergés sur le front matter existant. Si lastmod est fourni ici, il n'est pas écrasé par auto-lastmod."},
                },
                "required": ["route", "content"],
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
    ]}

async def handle_tool_call(params):
    tool_name = params.get("name", "")
    args      = params.get("arguments", {})

    tools = {
        "list_pages":  tool_list_pages,
        "get_page":    tool_get_page,
        "create_page": tool_create_page,
        "update_page": tool_update_page,
        "delete_page": tool_delete_page,
        "build_site":  tool_build_site,
    }

    tool = tools.get(tool_name)
    if not tool:
        raise HTTPException(404, f"Tool not found: {tool_name}")
    try:
        result = await tool(args)
        return {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]}
    except HTTPException as e:
        log.warning("tool_error tool=%s http=%s detail=%s", tool_name, e.status_code, e.detail)
        return {"content": [{"type": "text", "text": e.detail}], "isError": True}
    except Exception as e:
        log.error("tool_error tool=%s error=%s\n%s", tool_name, e, traceback.format_exc())
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
        md_files = list(scan_path.rglob("index.*.md"))
    except (PermissionError, OSError) as e:
        log.error("list_pages: walk failed: %s", e)
        return {"pages": [], "total": 0, "error": str(e)}

    for path in md_files:
        # stem of "index.fr.md" → "index.fr"
        stem_parts = path.stem.split('.')
        if len(stem_parts) != 2 or stem_parts[0] != 'index':
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
    route    = args.get("route", "")
    lang     = args.get("lang")
    filepath = find_page(route, lang)

    if not filepath:
        raise HTTPException(404, f"Page not found: {route} (lang={lang})")

    fm, content = read_frontmatter(filepath)
    return {
        "route":       route,
        "file":        filepath.replace(CONTENT_DIR + '/', ''),
        "frontmatter": fm,
        "content":     content,
    }

async def tool_create_page(args):
    route     = args.get("route", "").strip('/')
    lang      = args.get("lang", "fr")
    title     = args.get("title", "")
    content   = args.get("content", "")
    tags      = args.get("tags")
    draft     = args.get("draft")
    fm_custom = args.get("frontmatter")
    if isinstance(fm_custom, str):
        try:
            fm_custom = json.loads(fm_custom)
        except json.JSONDecodeError as e:
            log.warning("frontmatter invalid JSON string: %s", e)
            fm_custom = None

    filepath = f"{CONTENT_DIR}/{route}/index.{lang}.md"

    if Path(filepath).exists():
        raise HTTPException(409, f"Page already exists: {filepath}")

    # 1. Custom frontmatter as base
    final_fm: dict = {}
    if isinstance(fm_custom, dict):
        final_fm.update(fm_custom)

    # 2. Explicit params override
    final_fm["title"] = title
    if tags is not None:
        final_fm["tags"] = tags
    if draft is not None:
        final_fm["draft"] = draft

    # 3. date/lastmod: auto-generate only when not supplied in frontmatter
    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S+02:00")
    if "date" not in final_fm:
        final_fm["date"] = now
    if "lastmod" not in final_fm:
        final_fm["lastmod"] = now

    write_page(filepath, final_fm, content)
    deploy_output = run_deploy()
    cf_result     = await purge_cloudflare([f"/{route}/"])

    return {
        "status":   "created",
        "file":     filepath.replace(CONTENT_DIR + '/', ''),
        "deploy":   deploy_output,
        "cf_purge": cf_result,
    }

async def tool_update_page(args):
    route     = args.get("route", "")
    lang      = args.get("lang")
    content   = args.get("content", "")
    fm_custom = args.get("frontmatter")
    if isinstance(fm_custom, str):
        try:
            fm_custom = json.loads(fm_custom)
        except json.JSONDecodeError as e:
            log.warning("frontmatter invalid JSON string: %s", e)
            fm_custom = None

    filepath = find_page(route, lang)
    if not filepath:
        raise HTTPException(404, f"Page not found: {route}")

    # Start from existing frontmatter on disk
    fm, _ = read_frontmatter(filepath)

    # 1. Apply custom frontmatter fields on top
    if isinstance(fm_custom, dict):
        fm.update(fm_custom)

    # 2. Explicit params override
    if "title" in args:
        fm["title"] = args["title"]
    if "tags" in args:
        fm["tags"] = args["tags"]
    if "draft" in args:
        fm["draft"] = args["draft"]

    # 3. Auto-lastmod only if caller did not supply one
    if not (isinstance(fm_custom, dict) and "lastmod" in fm_custom):
        fm["lastmod"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S+02:00")

    write_page(filepath, fm, content)
    deploy_output = run_deploy()
    cf_result     = await purge_cloudflare([f"/{route.strip('/')}/"])

    return {
        "status":   "updated",
        "file":     filepath.replace(CONTENT_DIR + '/', ''),
        "deploy":   deploy_output,
        "cf_purge": cf_result,
    }

async def tool_delete_page(args):
    route    = args.get("route", "")
    lang     = args.get("lang")
    filepath = find_page(route, lang)

    if not filepath:
        raise HTTPException(404, f"Page not found: {route}")

    os.remove(filepath)

    parent = Path(filepath).parent
    if parent.exists() and not any(parent.iterdir()):
        parent.rmdir()

    deploy_output = run_deploy()
    cf_result     = await purge_cloudflare()

    return {
        "status":   "deleted",
        "file":     filepath.replace(CONTENT_DIR + '/', ''),
        "deploy":   deploy_output,
        "cf_purge": cf_result,
    }

async def tool_build_site(args):
    purge_cf      = args.get("purge_cf", True)
    deploy_output = run_deploy()
    cf_result     = await purge_cloudflare() if purge_cf else {"skipped": True}

    return {"status": "built", "deploy": deploy_output, "cf_purge": cf_result}

# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
