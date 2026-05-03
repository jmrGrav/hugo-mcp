#!/usr/bin/env python3
"""
Hugo MCP Server — FastAPI
Gère les pages Hugo depuis Claude.ai
"""

from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.responses import JSONResponse
import subprocess, os, glob, yaml, json
from pathlib import Path
from datetime import datetime
import httpx
from dotenv import load_dotenv

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
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    if not content.startswith('---'):
        return {}, content

    parts = content.split('---', 2)
    if len(parts) < 3:
        return {}, content

    try:
        fm = yaml.safe_load(parts[1]) or {}
    except Exception:
        fm = {}

    return fm, parts[2].strip()

def write_page(filepath: str, frontmatter: dict, content: str):
    fm_str       = yaml.dump(frontmatter, allow_unicode=True, default_flow_style=False)
    full_content = f"---\n{fm_str}---\n\n{content}\n"
    Path(filepath).parent.mkdir(parents=True, exist_ok=True)
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(full_content)

def find_page(route: str, lang: str = None) -> str | None:
    route = route.strip('/')

    patterns = []
    if lang:
        patterns += [
            f"{CONTENT_DIR}/{lang}/{route}/index.md",
            f"{CONTENT_DIR}/{route}/index.{lang}.md",
            f"{CONTENT_DIR}/{route}.{lang}.md",
        ]
    patterns += [
        f"{CONTENT_DIR}/{route}/index.md",
        f"{CONTENT_DIR}/{route}.md",
    ]

    for p in patterns:
        if Path(p).exists():
            return p
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
                    "route":   {"type": "string", "description": "Route ex: /posts/mon-article"},
                    "lang":    {"type": "string", "description": "Langue (fr, en)", "default": "fr"},
                    "title":   {"type": "string", "description": "Titre de la page"},
                    "content": {"type": "string", "description": "Contenu Markdown"},
                    "tags":    {"type": "array", "items": {"type": "string"}},
                    "draft":   {"type": "boolean", "default": False},
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
                    "route":   {"type": "string"},
                    "lang":    {"type": "string"},
                    "title":   {"type": "string"},
                    "content": {"type": "string"},
                    "tags":    {"type": "array", "items": {"type": "string"}},
                    "draft":   {"type": "boolean"},
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
        return {"content": [{"type": "text", "text": e.detail}], "isError": True}

# ── Tools ─────────────────────────────────────────────────────────────────────

async def tool_list_pages(args):
    lang    = args.get("lang")
    section = args.get("section")
    pages   = []

    for filepath in glob.glob(f"{CONTENT_DIR}/**/*.md", recursive=True):
        if os.path.basename(filepath).startswith('_'):
            continue

        fm, _ = read_frontmatter(filepath)
        rel   = filepath.replace(CONTENT_DIR + '/', '')

        if lang:
            basename    = os.path.basename(filepath)
            in_lang_dir = f"/{lang}/" in filepath
            lang_ext    = f".{lang}." in basename
            if not in_lang_dir and not lang_ext:
                continue

        if section and not rel.startswith(section):
            continue

        pages.append({
            "file":  rel,
            "title": fm.get("title", ""),
            "date":  str(fm.get("date", "")),
            "draft": fm.get("draft", False),
            "tags":  fm.get("tags", []),
        })

    pages.sort(key=lambda x: x.get("date", ""), reverse=True)
    return {"pages": pages, "total": len(pages)}

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
    route   = args.get("route", "").strip('/')
    lang    = args.get("lang", "fr")
    title   = args.get("title", "")
    content = args.get("content", "")
    tags    = args.get("tags", [])
    draft   = args.get("draft", False)

    filepath = f"{CONTENT_DIR}/{lang}/{route}/index.md"

    if Path(filepath).exists():
        raise HTTPException(409, f"Page already exists: {filepath}")

    frontmatter = {
        "title": title,
        "date":  datetime.now().strftime("%Y-%m-%dT%H:%M:%S+02:00"),
        "draft": draft,
        "tags":  tags,
    }

    write_page(filepath, frontmatter, content)
    deploy_output = run_deploy()
    cf_result     = await purge_cloudflare([f"/{lang}/{route}/"])

    return {
        "status":   "created",
        "file":     filepath.replace(CONTENT_DIR + '/', ''),
        "deploy":   deploy_output,
        "cf_purge": cf_result,
    }

async def tool_update_page(args):
    route   = args.get("route", "")
    lang    = args.get("lang")
    content = args.get("content", "")

    filepath = find_page(route, lang)
    if not filepath:
        raise HTTPException(404, f"Page not found: {route}")

    fm, _ = read_frontmatter(filepath)

    if "title" in args:
        fm["title"] = args["title"]
    if "tags" in args:
        fm["tags"] = args["tags"]
    if "draft" in args:
        fm["draft"] = args["draft"]

    fm["lastmod"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S+02:00")

    write_page(filepath, fm, content)
    deploy_output = run_deploy()
    route_clean   = route.strip('/')
    cf_paths      = [f"/{lang}/{route_clean}/"] if lang else [f"/{route_clean}/"]
    cf_result     = await purge_cloudflare(cf_paths)

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
