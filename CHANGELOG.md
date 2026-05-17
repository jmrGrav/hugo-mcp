# Changelog

## [2.1.0] — 2026-05-18

### Added
- **Plugin audit events** — `HugoMcpPlugin.on_audit(audit_type, context)` hook (default no-op for backward compatibility) and `PluginRegistry.fire_audit_event()` dispatcher with a 10 min timeout. Lets plugins opt in to non-page events (scheduled checks, on-demand audits) by setting `handles_audit = True`. Existing plugins are unaffected.
- **`check_sri_versions` MCP tool** — Diagnoses SRI hashes (live re-check) and npm version freshness for CDN libs used by the Hugo site. Args: `auto_fix` (default `false`) and `dry_run` (default `false`).
- **`sri-check` plugin** (`plugins/sri-check/`) — Wraps the standalone `check-sri-versions.sh` script via subprocess with `--json` mode. On successful auto-fix, fires a synthetic `updated` page event with `force_full_purge=True` so the Cloudflare plugin handles cache purge (rather than the script doing it inline) — fine-grained orchestration.

### Changed
- **`generate_featured_image` MCP tool** — API change: now takes a `slug` argument (lowercase alphanumeric + hyphens) instead of `filename`. The filename is derived internally as `{slug}-featured.jpg`. Simpler for callers, prevents path-traversal attempts more strictly. **Breaking** if you previously called with `filename=...`.
- **Cloudflare plugin** — Now honours `context.force_full_purge: True` to override `mode` and perform a full zone purge regardless of the configured mode. Used by the SRI auto-fix orchestration.
- **`check-sri-versions.sh`** — Adds `--json`, `--no-autofix`, `--no-cf-purge`, `--dry-run` flags. The `--json` flag appends a `===JSON-REPORT===` marker followed by a single JSON object summarizing diagnostic + auto-fix + incident lifecycle.

### Notes
- The weekly cron (`/home/jm/scripts/check-sri-versions.sh`, Mon 08:00) continues to run autonomously and is independent of the MCP. The MCP tool is an on-demand façade that reuses the same script logic.

## [1.9.0] — 2026-05-09

### Security
- **C1** : slowapi 60 req/min per client IP (`X-Real-IP` header from nginx proxy)
- **C2** : Multi-token auth system — `tokens.json` with bcrypt cost-12 hashes; `token_mgr.py` CLI (`list`, `add`, `revoke`, `migrate`)
- **C3** : structlog JSON audit events — `write_op` and `timing` events as machine-readable JSON (Vector/BetterStack ready)
- **C4** : Pydantic v2 input models (`CreatePageArgs`, `UpdatePageArgs`) — title max 500, content max 512 KB, tags max 50×100 chars, route min/max length
- **C5** : bcrypt cost-12 for tokens stored in `tokens.json` (via C2 token system)
- **C6** : TLS NUC↔VM — EC P-256 self-signed cert on VM (`tls/server.crt`), uvicorn `--ssl-certfile/keyfile`, nginx proxy verifies cert via `MCP_CA_CERT`
- **C7** : `requirements.lock` with SHA-256 hashes via `pip-compile --generate-hashes`
- **C8** : Docs disabled (`docs_url=None`, `redoc_url=None`, `openapi_url=None`); generic exception handler (no traceback leak); nginx `proxy_hide_header Server/X-Powered-By`
- **C9** : nginx `if` enforcement for `/mcp` — method must be POST (405), Content-Type must contain `application/json` (415); OWASP CRS (ModSec) applies globally
- **C10** : `backup.sh` — GPG-encrypted tar of config + content + tokens.json (no private key), 30-day retention

### Added
- `token_mgr.py` — CLI for token lifecycle management (add/revoke/list/migrate)
- `backup.sh` — DR backup script with GPG encryption and retention
- `requirements.lock` — hashed lockfile for supply chain verification

## [1.8.0] — 2026-05-09

### Added
- `list_assets` tool — liste les fichiers statiques (`static/`) et les ressources des page bundles (`content/`) avec filtres `path_prefix` et `type`
- Support de la route racine : `route='/'` ou `route='_index'` pour cibler `_index.{lang}.md`
- Timing instrumentation pour les opérations write / build / purge (logs structurés avec durées ms)

### Docs
- Backlogs sécurité et webhook ajoutés dans `docs/`

## [1.7.0] — 2026-05-07

### Added
- `frontmatter` parameter now fully exposed in `create_page` and `update_page` schemas (object libre, validé côté serveur)
- `update_page` : sémantique deep merge — les dict imbriqués sont mergés récursivement ; `null` supprime un champ existant
- `_deep_merge()` helper pour merge récursif avec sentinelle null

### Security
- Validation stricte du frontmatter : taille (10 KB), profondeur (3 niveaux), types autorisés, clés non-string → HTTP 400
- Détection de conflit entre param dédié (title/tags/draft) et frontmatter → HTTP 400
- Champ `date` immuable sur `update_page` → HTTP 400 si tentative de modification
- `null` refusé sur `create_page` (uniquement valide sur `update_page` pour suppression)

### Notes
- Rétrocompatible : omettre `frontmatter` conserve le comportement précédent
- `date` et `lastmod` auto-générés si absents du frontmatter fourni

## [1.6.0] — 2026-05-07

### Added
- **H-11 LOW** : endpoints `/healthz`, `/readyz`, `/metrics` — `/metrics` restreint à loopback + 192.168.122.1 (Prometheus-compatible)

## [1.5.1] — 2026-05-07

### Security
- **H-10 LOW** : `_validate_frontmatter()` — blacklist des champs sensibles (`aliases`, `cascade`, `build`, `outputs`, `headless`, `_target`) ; HTTP 400 si l'un d'eux est présent dans le param `frontmatter`

## [1.5.0] — 2026-05-07

### Security
- **H-06 LOW** : audit log des opérations write (`create_page`, `update_page`, `delete_page`) via `logging.getLogger("hugo-mcp.audit")` → journald ; champ `ip` inclus
- **H-08 LOW** : `verify_token()` utilise `hmac.compare_digest` (constant-time) pour éviter les timing attacks

## [1.4.0] — 2026-05-07

### Security
- **H-04 MEDIUM** : Port 8000 déjà restreint par UFW (`allow from 192.168.122.1`, default deny incoming) — finding confirmé clos, aucun code changé
- **H-05 MEDIUM** : middleware `limit_request_body` — rejette les bodies > 512 KB avec HTTP 413
- **H-07 MEDIUM** : starlette 0.38.6 → 1.0.0, fastapi 0.115.0 → 0.136.1 (corrige CVE-2024-47874 ReDoS)

## [1.3.1] — 2026-05-07

### Security
- **H-01 CRITICAL** : `_safe_route()` — validation par `Path.resolve().relative_to()` avant toute opération FS ; 400 si la route tente d'échapper `CONTENT_DIR`
- **H-09 LOW** : `_safe_lang()` — whitelist regex `^[a-z]{2,3}$` ; 400 si le code de langue est invalide ou contient des caractères spéciaux
- Les deux validateurs sont appelés au début de `tool_create_page`, `tool_update_page`, `tool_delete_page`, `tool_get_page`

## [1.3.0] — 2026-05-04

### Breaking Change
- Convention de fichiers Hugo LoveIt : `content/{lang}/{route}/index.md` → `content/{route}/index.{lang}.md`
  LoveIt détecte les traductions par cohabitation dans le même dossier ; l'ancienne convention par sous-dossier de langue ne déclenchait pas le sélecteur de langue dans le menu.
- Migration manuelle requise pour le contenu existant :
  ```bash
  cd /home/jm/hugo-site/content
  for lang_dir in fr en; do
      find $lang_dir -mindepth 2 -name "index.md" | while read f; do
          slug=$(dirname "$f" | sed "s|^$lang_dir/||")
          mkdir -p "$slug"
          mv "$f" "$slug/index.$lang_dir.md"
      done
      find $lang_dir -type d -empty -delete
  done
  ```

### Changed
- `find_page` : cherche `index.{lang}.md` en priorité, fallback sur `index.md` (pages sans suffixe)
- `tool_list_pages` : scanne `index.*.md`, extrait la langue du nom de fichier ; retourne désormais `route` et `lang` dans chaque entrée
- `tool_create_page` : écrit dans `content/{route}/index.{lang}.md`
- `tool_update_page` / `tool_delete_page` : adaptés à la nouvelle convention via `find_page`
- Purge Cloudflare : chemin `/{route}/` (plus de préfixe `/{lang}/`)

## [1.2.1] — 2026-05-04

### Fixed
- `create_page` / `update_page` : `frontmatter` param désérialisé depuis JSON string si le transport MCP envoie un objet sérialisé (était silencieusement ignoré car `isinstance(..., dict)` → False)

## [1.2.0] — 2026-05-04

### Added
- `create_page` / `update_page` : nouveau paramètre `frontmatter` (dict libre) pour injecter description, url, categories, featuredImage, toc, date custom, et tout champ Hugo/thème
- Logique de merge : `frontmatter` en base, puis champs explicites `title`/`tags`/`draft` priment en cas de conflit
- `date` et `lastmod` auto-générés uniquement s'ils sont absents du `frontmatter` fourni → migration Grav préserve les dates d'origine
- `update_page` : `lastmod` n'est plus écrasé si fourni dans `frontmatter` (comportement précédent : toujours `now()`)

### Changed
- `write_page` : `yaml.safe_dump(..., sort_keys=False)` — préserve l'ordre des clés et l'unicode

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

[2.1.0]: https://github.com/jmrGrav/hugo-mcp/compare/v2.0.0...v2.1.0
[2.0.0]: https://github.com/jmrGrav/hugo-mcp/compare/v1.9.0...v2.0.0
[1.9.0]: https://github.com/jmrGrav/hugo-mcp/compare/v1.8.1...v1.9.0
[1.8.1]: https://github.com/jmrGrav/hugo-mcp/compare/v1.8.0...v1.8.1
[1.8.0]: https://github.com/jmrGrav/hugo-mcp/compare/v1.7.0...v1.8.0
[1.7.0]: https://github.com/jmrGrav/hugo-mcp/compare/v1.6.0...v1.7.0
[1.6.0]: https://github.com/jmrGrav/hugo-mcp/compare/v1.4.0...v1.6.0
[1.4.0]: https://github.com/jmrGrav/hugo-mcp/compare/v1.3.1...v1.4.0
[1.3.1]: https://github.com/jmrGrav/hugo-mcp/compare/v1.3.0...v1.3.1
[1.3.0]: https://github.com/jmrGrav/hugo-mcp/compare/v1.2.1...v1.3.0
[1.2.1]: https://github.com/jmrGrav/hugo-mcp/compare/v1.2.0...v1.2.1
[1.2.0]: https://github.com/jmrGrav/hugo-mcp/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/jmrGrav/hugo-mcp/compare/v1.0.1...v1.1.0
[1.0.1]: https://github.com/jmrGrav/hugo-mcp/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/jmrGrav/hugo-mcp/releases/tag/v1.0.0
