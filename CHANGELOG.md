# Changelog

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
