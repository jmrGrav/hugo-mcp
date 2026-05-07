# Phase 2bis — Rapport final — 2026-05-07

Audit sécurité complet (17 findings) réalisé en 3 sprints + 1 hotfix.
Versions finales : **Hugo MCP v1.6.0** | **Grav MCP v1.5.0**

---

## Récap des 17 findings

| ID | Sévérité | Description | Statut | Version | Commit |
|----|----------|-------------|--------|---------|--------|
| H-01 | CRITICAL | Path traversal via `route` param | ✅ Fixé | v1.3.1 | 4fad845 |
| H-02 | HIGH | (hors scope Phase 2bis) | — | — | — |
| H-03 | HIGH | (hors scope Phase 2bis) | — | — | — |
| H-04 | MEDIUM | Port 8000 exposé | ✅ Conforme (UFW existant) | v1.4.0 | — |
| H-05 | MEDIUM | Body size illimité | ✅ Fixé (512 KB limit) | v1.4.0 | 059a3e1 |
| H-06 | LOW | Pas d'audit log des write ops | ✅ Fixé (hugo-mcp.audit → journald) | v1.5.0 | bbef1b7 |
| H-07 | MEDIUM | starlette 0.38.6 CVE-2024-47874 | ✅ Fixé (→ 1.0.0) | v1.4.0 | 059a3e1 |
| H-08 | LOW | Token comparison non constant-time | ✅ Fixé (hmac.compare_digest) | v1.5.0 | bbef1b7 |
| H-09 | LOW | Validation code langue absente | ✅ Fixé (`_safe_lang()` whitelist regex) | v1.3.1 | 4fad845 |
| H-10 | LOW | Frontmatter sensible injectable | ✅ Fixé (`_validate_frontmatter()` blacklist) | v1.5.1 | bbef1b7 |
| H-11 | LOW | Pas d'endpoints monitoring | ✅ Fixé (`/healthz` `/readyz` `/metrics`) | v1.6.0 | bbef1b7 |
| G-01 | CRITICAL | Path traversal via `route` param (Grav) | ✅ Fixé (`_safe_route_path()` segments) | v1.3.0 | 6710397 |
| G-02 | HIGH | Suppression de page sans filtre langue | ✅ Fixé (param `lang`, suppression sélective) | v1.3.0 | 6710397 |
| G-03 | MEDIUM | `initialize`/`tools/list` sans auth | ✅ Fixé (config `strict_auth_on_initialize`) | v1.4.0 | 99a009e |
| G-04 | LOW | Pas d'audit log des write ops (Grav) | ✅ Fixé (`logAudit()` → error_log PHP) | v1.5.0 | d2b7235 |
| G-05 | LOW | Token comparison non constant-time (Grav) | ✅ Fixé (`hash_equals()`) | v1.5.0 | d2b7235 |
| G-06 | LOW | (hors scope Phase 2bis) | — | — | — |

---

## Détail par sprint

### Sprint 1 — Hugo MCP v1.3.1 + Grav MCP v1.3.0

**H-01 CRITICAL** : `_safe_route()` — `Path.resolve().relative_to(CONTENT_DIR)` ; HTTP 400 si path traversal.
**H-09 LOW** : `_safe_lang()` — regex `^[a-z]{2,3}$` ; HTTP 400 si code langue invalide.
**G-01 CRITICAL** : `_safe_route_path()` — vérification segment par segment (`..` et `.` interdits) ; RuntimeException → HTTP 400.
**G-02 HIGH** : `toolDeletePage()` — suppression sélective par lang ; dossier supprimé seulement si plus aucun `.md`.

### Sprint 2 — Hugo MCP v1.4.0 + Grav MCP v1.4.0

**H-04 MEDIUM** : UFW existant vérifié conforme — `allow from 192.168.122.1`, default deny incoming. Aucun changement.
**H-05 MEDIUM** : middleware `limit_request_body` — rejette les bodies > 512 KB avec HTTP 413.
**H-07 MEDIUM** : starlette 0.38.6 → 1.0.0, fastapi 0.115.0 → 0.136.1 (CVE-2024-47874 ReDoS).
**G-03 MEDIUM** : config `strict_auth_on_initialize` (OFF par défaut) — si ON, toutes les méthodes MCP exigent un token.

### Hotfix pré-Sprint 3 (2026-05-07)

Permissions filesystem Hugo : `chown -R hugo-mcp:hugo-mcp` sur `hugo-site/`, `content/`, `public/`, `resources/`.
Suppression du lock orphelin `hugo-site/.hugo_build.lock` (créé avant Sprint 1, jamais rechowned).
`ReadWritePaths` simplifié : `/home/jm/hugo-site` (couvre tout le sous-arbre).
**Leçon** : validation obligatoire depuis Claude.ai (pas seulement SSH) pour tout fix touchant systemd ou permissions.

### Sprint 3 — Hugo MCP v1.5.0→v1.6.0 + Grav MCP v1.5.0

**H-06 LOW** : `audit_log = logging.getLogger("hugo-mcp.audit")` — log create/update/delete + IP → journald.
`journalctl -u hugo-mcp | grep "hugo-mcp.audit"` pour filtrer.

**H-08 LOW** : `verify_token()` — `hmac.compare_digest(token.encode(), MCP_TOKEN.encode())`.

**H-10 LOW** : `_validate_frontmatter()` — blacklist `{"aliases", "cascade", "build", "outputs", "headless", "_target"}` ; HTTP 400 si présent.
Appelé dans `tool_create_page` et `tool_update_page` après parsing JSON.

**H-11 LOW** : Endpoints monitoring loopback-only :
- `/healthz` — toujours 200 `{"status":"ok"}`
- `/readyz` — 503 si `CONTENT_DIR` absent
- `/metrics` — format Prometheus ; 403 si IP hors `{127.0.0.1, 192.168.122.1}`
`METRICS` dict pour comptage create/update/delete/errors.

**G-04 LOW** : `logAudit()` — `error_log('[grav-mcp.audit] action=... route=... lang=... ip=...')` pour create/update/delete.

**G-05 LOW** : `authenticateOAuth()` — `!hash_equals('Bearer ' . $token, $auth ?? '')`.

---

## Versions et tags GitHub

| Repo | Version | Tag | Commit |
|------|---------|-----|--------|
| hugo-mcp | v1.3.1 | ✅ | 4fad845 |
| hugo-mcp | v1.4.0 | ✅ | 059a3e1 |
| hugo-mcp | v1.6.0 | ✅ | bbef1b7 |
| grav-plugin-mcp-server | v1.3.0 | ✅ | 6710397 |
| grav-plugin-mcp-server | v1.4.0 | ✅ | 99a009e |
| grav-plugin-mcp-server | v1.5.0 | ✅ | d2b7235 |

---

## Validation requise depuis Claude.ai (STOP)

Hugo MCP Sprint 3 est déployé sur la VM (systemd restart effectué).
Tests SSH déjà verts : `/healthz`, `/readyz`, `/metrics` (loopback).

**Avant de clore Phase 2bis**, Jm doit valider depuis Claude.ai :
1. `Hugo MCP:create_page` — vérifier que build est clean ET que les audit logs apparaissent dans `journalctl -u hugo-mcp | grep audit`
2. `Hugo MCP:update_page` — idem
3. `Hugo MCP:delete_page` — idem
4. `Hugo MCP:create_page` avec `frontmatter: {"aliases": ["/test"]}` → doit retourner HTTP 400 (H-10)
5. Grav MCP : `create_page` / `update_page` / `delete_page` → fonctionnel

---

## Ce qui reste (Phase 3+)

- Phase 3 : Auto-SEO via Claude Haiku (IndexNow, balises méta, images featuredImage)
- Phase 4 : Sitemap multilingue
- Phase 5 : Migration Grav → Hugo complète
- H-02, H-03, G-06 : hors scope Phase 2bis
