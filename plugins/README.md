# hugo-mcp plugins

Plugins for hugo-mcp implementing the `HugoMcpPlugin` contract from `core/plugin_base.py`.

Each plugin fires after `create_page`, `update_page`, or `delete_page` operations.
Plugins run in parallel with a per-plugin timeout (10 s default). A plugin failure
never blocks the MCP response — errors are returned in the `plugins` field.

## Bundled plugins

| Plugin | Purpose |
|--------|---------|
| `indexnow/` | Submit URLs to Bing/Yandex via [IndexNow](https://www.indexnow.org/) protocol |
| `google-indexing/` | Submit URLs to Google Indexing API v3 (JWT RS256 auth, daily quota tracking) |
| `cloudflare/` | Purge Cloudflare cache — `full`, `partial`, or `smart` mode |
| `_template/` | Skeleton for writing your own plugin |

## Enabling a plugin

Edit `config/plugins.yaml` (copy from `config/plugins.example.yaml`):

```yaml
cloudflare:
  enabled: true
  mode: smart
  api_token_env: CF_TOKEN
  zone_id: YOUR_ZONE_ID
  base_url: https://example.com
```

Then restart hugo-mcp:

```bash
sudo systemctl restart hugo-mcp
sudo journalctl -u hugo-mcp --since 30 sec ago | grep plugins.activated
```

## Writing your own plugin

1. Copy the skeleton: `cp -r plugins/_template plugins/my-plugin`
2. Implement the three abstract methods of `HugoMcpPlugin`:

   ```python
   def is_enabled(self, config: dict) -> bool: ...
   def validate_config(self, config: dict) -> tuple[bool, str]: ...
   async def on_page_event(self, event_type, urls, context) -> dict: ...
   ```

3. Add a section under your plugin name in `config/plugins.yaml`
4. Restart hugo-mcp — your plugin appears in `plugins.activated` log

See `core/plugin_base.py` for the full contract and `core/plugin_loader.py` for
auto-discovery logic.
