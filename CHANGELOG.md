# Changelog

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
