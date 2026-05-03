# hugo-mcp

MCP server for Hugo static site management — `hugo-test.arleo.eu`.

## Tools

| Tool | Description |
|------|-------------|
| `list_pages` | List all Hugo pages (filter by lang/section) |
| `get_page` | Read frontmatter + Markdown content |
| `create_page` | Create page + rebuild + Cloudflare purge |
| `update_page` | Update page + rebuild + Cloudflare purge |
| `delete_page` | Delete page + rebuild + full CF purge |
| `build_site` | Rebuild Hugo + full CF purge |

## Setup

```bash
python3 -m venv venv
venv/bin/pip install -r requirements.txt
# Créer .env avec MCP_TOKEN, CF_TOKEN, CF_ZONE_ID
sudo cp systemd/hugo-mcp.service /etc/systemd/system/
sudo systemctl enable --now hugo-mcp
```

## Architecture

```
Claude.ai → Cloudflare → nginx → mcp-oauth-proxy (:8084) → FastAPI (:8000) → hugo-site
```

## License

MIT
