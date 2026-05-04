# Changelog

## [1.1.0] — 2026-05-04

### Changed
- Structured logger (`hugo-mcp`) added — all tool errors now logged with level + message
- `handle_tool_call` catches broad `Exception` with full traceback in logs; returns `{type}: {message}` + `isError: true` to client instead of generic crash
- `handle_tool_call` logs HTTP tool errors (404, 409…) at WARNING level
- `read_frontmatter` uses `yaml.YAMLError` (typed) instead of bare `except`; non-dict YAML values fall back to `{}`
- `tool_list_pages` guards against absent `CONTENT_DIR` (returns `[]` cleanly)
- `tool_list_pages` uses `Path.rglob` with `PermissionError`/`OSError` handling; unreadable files are skipped individually and counted in `skipped` field

## [1.0.1] — 2026-05-03

### Fixed
- `tools/call` responses now wrapped in MCP content format `{"content": [{"type": "text", "text": "..."}]}` as required by the MCP spec — Claude.ai was rejecting raw dict results with "Error occurred during tool execution"
- Tool-level errors return `isError: true` in content instead of JSON-RPC protocol errors

## [1.0.0] — 2026-05-03

### Added
- `list_pages` — liste les pages Hugo avec filtres lang/section
- `get_page` — lit frontmatter YAML + contenu Markdown
- `create_page` — crée une page + rebuild + purge CF ciblée
- `update_page` — met à jour frontmatter/contenu + rebuild + purge CF ciblée
- `delete_page` — supprime une page + rebuild + purge CF totale
- `build_site` — rebuild Hugo + purge CF totale
- Auth bearer token (`MCP_TOKEN`)
- Support bilingue fr/en (hugo.toml LoveIt)
- Service systemd `hugo-mcp.service`
