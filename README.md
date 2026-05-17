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
| `upload_asset` | Upload an image to `static/` |
| `list_assets` | List static assets and page bundles |
| `generate_featured_image` | Generate Tokyo Night featured image (dark+light) via `arleo-image` skill |
| `check_sri_versions` | Audit SRI hashes + npm versions of CDN libs (diagnose / auto-fix minor/patch) |

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

## Audit plugins

Plugins can opt in to **non-page events** by setting `handles_audit = True` and overriding `on_audit(audit_type, context)`. The registry exposes `fire_audit_event(audit_type, context)` which dispatches to all audit-handler plugins with a 10 min timeout (separate from the page-event timeout).

### `sri-check` plugin (audit_type=`sri_check`)

Audits Subresource Integrity hashes and npm version freshness for CDN libs (jsdelivr) referenced by the Hugo site. Wraps the standalone bash script `check-sri-versions.sh`.

- **Diagnostic (default)** — re-fetch each pinned CDN URL, compute SHA-256, compare with stored hash ; query `https://data.jsdelivr.com/v1/packages/npm/<pkg>` for latest version ; classify outdated as `minor/patch` (auto-fixable) vs `major` (manual review). Hash mismatch is always WARN (security signal).
- **Auto-fix (auto_fix=true)** — bumps minor/patch outdated libs in `assets/data/cdn/jsdelivr.yml` + `data/sri.yaml`, rebuilds Hugo with `--cleanDestinationDir`, deploys, fires an `updated` page event with `force_full_purge=True` so the Cloudflare plugin handles cache purge (fine-grained orchestration). Verifies live hashes ; rolls back on failure.
- **Notification** — On WARN, the bash script POSTs a BetterStack incident and tracks the ID in `/home/jm/.config/sri-check.open-incidents` ; on next OK run, it auto-resolves any tracked incident.

```yaml
sri_check:
  enabled: true
  script_path: /home/jm/scripts/check-sri-versions.sh
  trigger_cf_purge_on_fix: true
```

The weekly cron continues to run independently of the MCP, so audits keep happening even if the MCP is down. The MCP tool is an on-demand façade.

## Architecture

```
Claude.ai → Cloudflare → nginx → mcp-oauth-proxy (:8084) → FastAPI (:8000) → hugo-site
```

## License

MIT
