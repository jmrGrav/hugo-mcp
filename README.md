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

## Custom frontmatter

`create_page` and `update_page` accept an optional `frontmatter` parameter (free-form dict) for Hugo fields not covered by dedicated params: `description`, `categories`, `featuredImage`, `toc`, `date`, `lastmod`, etc.

```python
# Create with custom fields
create_page(route="/posts/my-post", title="...", content="...",
            frontmatter={"description": "...", "featuredImage": "/img.jpg"})

# Update: deep merge — only changed fields needed
update_page(route="/posts/my-post", content="...",
            frontmatter={"description": "Updated desc"})

# Delete a field: use null
update_page(route="/posts/my-post", content="...",
            frontmatter={"featuredImage": null})
```

**Validation rules:**
- Forbidden fields (security): `aliases`, `cascade`, `build`, `outputs`, `headless`, `_target` → HTTP 400
- Conflict with dedicated param (`title`, `tags`, `draft`): if provided both ways → HTTP 400
- Max size: 10 KB serialized
- Max depth: 3 levels
- Allowed value types: string, number, boolean, list, dict (or null on `update_page` only)
- `date` is immutable on `update_page` — use original creation date
- `date` and `lastmod` are auto-generated if absent from frontmatter

## Architecture

```
Claude.ai → Cloudflare → nginx → mcp-oauth-proxy (:8084) → FastAPI (:8000) → hugo-site
```

## License

MIT
