# Backlog Arleo MCP Stack — Mai 2026

**Document de référence** consolidant tous les briefs et chantiers liés à l'écosystème MCP d'Arleo (Grav MCP, Hugo MCP, OAuth proxy, migration Grav → Hugo, et installer).

**Dernière mise à jour** : 2026-05-07

---

## 🟢 Livré (référence historique)

### Phase 2bis — Audit sécurité complet (17 findings)

| Sprint | Findings | Versions livrées |
|---|---|---|
| Sprint 1 (CRITICAL/HIGH) | H-01, H-09, H-03, G-01, G-02, H-02 | Hugo MCP v1.3.1, Grav MCP v1.3.0 |
| Hotfix permissions | Lock orphelin + chown root + ReadWritePaths | (pas de bump) |
| Sprint 2 (MEDIUM) | H-04 (UFW), H-05 (body limit), H-07 (CVE starlette), G-03 (strict auth toggle) | Hugo MCP v1.4.0, Grav MCP v1.4.0 |
| Sprint 3 (LOW + observabilité) | H-06 + G-04 (audit logs), H-08 + G-05 (token compare), H-10 (frontmatter blacklist), H-11 (metrics) | Hugo MCP v1.6.0, Grav MCP v1.5.0 |

**Tags GitHub** poussés sur les deux repos.

### Frontmatter exposé dans le schéma Hugo MCP

- v1.7.0 backend Hugo MCP : `frontmatter` librement passable, validé serveur (types/profondeur/taille/blacklist), conflit avec params dédiés détecté en 400, merge profond sur update_page avec sentinelle `null`, champ `date` immuable
- Tag GitHub poussé

### Refactor proxy MCP OAuth v2.0.0

- **Cause racine identifiée** : les deux proxies (port 8083 Grav, port 8084 Hugo) partageaient un `_tools_cache` en mémoire, chargé au startup, jamais invalidé. Toute évolution du schéma backend était invisible aux clients MCP.
- **Stratégie 4** retenue : suppression complète du cache de schémas. Le proxy devient transparent — il authentifie, c'est tout.
- Suppression : `_tools_cache`, `_fetch_tools()`, warm-up au démarrage
- `tools/list` désormais relayé directement au backend comme toute autre requête
- Bénéfice : toute future modification du schéma backend (Hugo MCP ou Grav) est visible dans Claude.ai immédiatement après reconnexion du connecteur, sans toucher au proxy
- Tag v2.0.0 poussé

### Migration Grav → Hugo (3/14 pages)

| # | Page | FR | EN | URL Hugo |
|---|---|---|---|---|
| 1 | csp-nonce | ✅ | ✅ | /csp-nonce/, /en/csp-nonce/ |
| 2 | crowdsec-cloudflare-waf-autoban | ✅ | ✅ | /crowdsec-cloudflare-waf-autoban/, /en/... |
| 3 | vector-logs-harmonisation-betterstack | ✅ | ✅ | /vector-logs-harmonisation-betterstack/, /en/... |
| 4 | crowdsec-vector-pipeline | ✅ | ✅ | /crowdsec-vector-pipeline/, /en/... |

---

## 🟡 En cours

### Migration Grav → Hugo (10 pages restantes)

Liste à attaquer (dans l'ordre numéroté Grav) — toutes ont leur version EN sur Grav, à vérifier au cas par cas avant migration :

- jquery-migration
- post-mortem-522-wan-failover
- hugo-vm-installation
- media-vm-migration
- grav-plugin-indexnow
- grav-plugin-google-indexing
- grav-mcp-server
- documentation
- scripts
- home (`_index.md` à la racine du content)
- privacy-policies
- hugo-mcp-server

**Workflow par page** :
1. `Arleo:get_page` lang=fr → contenu FR markdown via conversion HTML→MD LoveIt
2. `Arleo:get_page` lang=en → contenu EN (si présent — toujours vérifier)
3. `Hugo MCP:create_page` × 2 (FR + EN) avec frontmatter custom (description, categories, featuredImage, lastmod)
4. Validation visuelle des URLs vivantes

**Convention frontmatter** :
- `categories: ["Infrastructure"]` (ou autre selon le sujet)
- `description` reprise du metadata Grav (ou réécrite si générique)
- `lastmod` : date Grav au format ISO 8601
- `featuredImage: ""` (vide pour l'instant — génération AI à venir)
- `toc: { auto: true, enable: true }`

**Convention tags** :
- Normalisés sans accent : `securite` (pas `sécurité`), `reseau` (pas `réseau`)

---

## 🔴 À faire (chantiers planifiés, par ordre de priorité)

### Chantier A — Phases 3-5 du brief Hugo MCP original

Source : `brief-claude-code-mcp-extensions.md` (généré le 2026-05-07).

#### Phase 3 — Auto-SEO via Claude Haiku 4.5

Génération automatique de `description` (150-160 chars, optimisée Google) et `keywords` (4-6 par page) via appel à l'API Anthropic depuis le MCP.

- Modèle : `claude-haiku-4-5-20251001`
- Param ajouté : `auto_seo: bool = False` sur `create_page` et `update_page`
- Var d'env requise : `ANTHROPIC_API_KEY` dans le `.env` du MCP
- Coût estimé : ~0.001 $ par page
- Comportement : si description ou keywords absents ET `auto_seo=True`, génération auto. Si fournis, pas d'override.
- Graceful failure : si l'API LLM échoue, pas de blocage — la page se crée sans SEO auto, à compléter manuellement
- Bump version cible : Hugo MCP v1.7.0 → v1.8.0

À ajouter aussi côté Grav MCP (logique partagée, à factoriser dans un module `seo.py` si possible).

#### Phase 4 — IndexNow + Google Indexing API (pour Hugo)

Notification automatique des moteurs de recherche au create/update de page.

**Param ajouté** : `ping_seo: bool = False` (par défaut OFF, pour ne pas spammer pendant les tests).

**Triple protection anti-indexation du site de test** (CRITIQUE — sinon `hugo-test.arleo.eu` sera indexé en parallèle de `arleo.eu`) :

1. Var dédiée `SEO_PUBLIC_HOST` séparée de `baseURL` Hugo
2. Kill-switch `SEO_PINGS_ENABLED=false` par défaut
3. Refus explicite des hosts contenant `test`, `dev`, `staging`, `preview`, `localhost`
4. Cross-check avec `hugo.toml` baseURL (refus si baseURL contient un keyword interdit)
5. `static/robots.txt` avec `Disallow: /` sur le site de test
6. Meta `noindex` injecté quand baseURL contient "test"

**IndexNow** :
- Endpoint : `https://api.indexnow.org/indexnow`
- Couvre Bing, Yandex, Naver, Seznam
- Var d'env : `INDEXNOW_KEY` (32 hex)
- Pré-requis : fichier `static/{INDEXNOW_KEY}.txt` créé automatiquement au premier run

**Google Indexing API** :
- Service Account JSON requis (path dans `.env` : `GOOGLE_INDEXING_SERVICE_ACCOUNT_JSON`)
- Vérification de propriété du domaine dans Google Search Console
- Note : `URL_UPDATED` n'est officiellement supporté que pour JobPostings/BroadcastEvents — best effort pour blogs

**Tests obligatoires** : 6 scénarios couvrant kill-switch, baseURL test, host non-prod, prod réel.

**Validation `tcpdump`** : aucun paquet ne doit sortir vers `api.indexnow.org` ni `indexing.googleapis.com` pendant les tests bloquants.

**Bump version cible** : v1.8.0 → v1.9.0

#### Phase 5 — Sitemap hreflang multilingue

Override du template Hugo `sitemap.xml` (modif côté repo `/home/jm/hugo-site`, **pas côté MCP**) pour ajouter les annotations `hreflang` :

```xml
<url>
  <loc>https://arleo.eu/csp-nonce/</loc>
  <xhtml:link rel="alternate" hreflang="fr" href="..." />
  <xhtml:link rel="alternate" hreflang="en" href="..." />
  <xhtml:link rel="alternate" hreflang="x-default" href="..." />
  <lastmod>...</lastmod>
</url>
```

Validation : `xmllint --noout` + grep manuel des balises hreflang sur quelques pages échantillons.

**Bump version** : pas de MCP impacté (modif site Hugo direct), juste un commit dans le repo `hugo-site`.

---

### Chantier B — Création repo `jmrGrav/mcp-installer`

Source : `brief-claude-code-mcp-installer.md` (généré le 2026-05-07).

**Objectif** : transformer le stack MCP en produit installable par des tiers.

**Architecture validée** :
- Repo dédié `jmrGrav/mcp-installer`
- Script principal `install.sh` + lib/common.sh + 3 modules (install-grav-mcp.sh, install-hugo-mcp.sh, install-oauth-proxy.sh)
- Templates : systemd, nginx vhosts, .env
- OS supportés : Ubuntu 22.04+, Debian 12+, Fedora 38+, Rocky/Alma 9+
- Mode : hybride (interactif par défaut, `--silent` pour CI)
- Tokens : générés auto via `openssl rand`
- nginx : non touché, vhosts fournis comme templates à inclure
- Lancement : curl one-liner OU git clone

**Phases d'implémentation** (8 phases, ~10h Claude Code total) :

1. Phase A : squelette + lib + stubs (1h)
2. Phase B : `install-hugo-mcp.sh` complet (2h)
3. Phase C : `install-oauth-proxy.sh` (1h30)
4. Phase D : `install-grav-mcp.sh` (1h)
5. Phase E : `install.sh` orchestrateur + menu (1h)
6. Phase F : templates (1h)
7. Phase G : tests + doc (1h)
8. Phase H (par Jm, hors Claude Code) : article tutoriel sur arleo.eu

**Important — modifications post-refactor proxy** :
- Le brief original mentionnait `MCP_PROXY_ADMIN_TOKEN` pour invalider un cache. **Ce token n'existe plus** (le cache a été supprimé en stratégie 4). Mettre à jour le brief avant de lancer Claude Code.
- Le brief mentionnait `enableTLS` et `nftables` dans certains endroits. À cohérencer avec la décision finale "nginx non touché par l'installer".

---

### Chantier C — Idées en attente, non priorisées

Notes accumulées pendant les sessions, à évaluer plus tard :

- **Harmonisation `lang` / `language`** : Hugo MCP utilise `lang`, Grav MCP utilise `language` dans certains schémas. Asymétrie à corriger pour cohérence client.
- **Mise à jour du README Grav MCP** : refléter v1.5.0 (Sprint 3) avec mention des paramètres `lang` et des nouveaux comportements
- **Pattern Pydantic partagé** : aujourd'hui le schéma exposé et la validation côté serveur sont dérivés de deux sources différentes. À refactorer pour avoir un Pydantic model partagé qui dérive automatiquement le schéma JSON-RPC ET la validation
- **Endpoint `/admin/refresh` sur les MCPs eux-mêmes** : pour permettre à un admin de déclencher un rebuild de cache interne (futur, si on en remet)
- **VirusTotal integration** : scorer les IPs avant report AbuseIPDB
- **BetterStack alerts** : sur les patterns `cs.ip` récurrents (campagnes coordonnées)
- **Dashboard BetterStack `cs.origin`** : visualiser proportion CAPI / cscli / nginx-bouncer

---

## ⚠️ Règles transverses (héritées des sprints précédents)

À respecter pour TOUT chantier futur :

1. **Commits GPG-signés** : `sudo -u jm -E git commit -S` (le `-E` préserve `GPG_AGENT_INFO`)
2. **STOP et validation entre chaque phase** : pas de bulldozer
3. **Tests obligatoires avant push** : cas légitime + cas pathologique
4. **Validation finale via Claude.ai obligatoire** pour tout fix touchant systemd, permissions, ou schéma. **Pas de ✅ tant que le test Claude.ai n'est pas passé.** (Apprentissage du hotfix permissions du 7 mai où SSH passait mais Claude.ai échouait)
5. **Ne jamais publier l'IP statique du serveur** sur arleo.eu — utiliser une forme masquée type `82.XX.XX.XX`
6. **Ne jamais publier de tokens** (MCP, Cloudflare, BetterStack, CrowdSec) sur le site
7. **Pour les MCPs** : ne pas faire confiance aux memories quand on peut vérifier en live (`Arleo:get_page lang=en` pour confirmer l'existence d'une traduction avant de la générer)

---

## Résumé exécutif (pour quiconque revient sur ce projet)

L'écosystème MCP d'Arleo est un stack mature, durci, et fonctionnel, composé de :

- **3 MCPs** : Grav (CMS WordPress-like), Hugo (générateur statique), OAuth proxy
- **2 sites** : `www.arleo.eu` (Grav, prod) et `hugo-test.arleo.eu` (Hugo, pré-prod, en cours de migration)
- **17 findings sécurité** corrigés sur audit complet
- **Architecture proxy transparente** (refactor v2.0)
- **Param `frontmatter` libre** sur Hugo MCP avec validation stricte serveur

Ce qui reste à faire :
- Finir la migration des 10 dernières pages Grav → Hugo
- Ajouter auto-SEO LLM, IndexNow, sitemap hreflang
- Créer le repo `mcp-installer` pour ouvrir le stack à des tiers
- Bascule finale du DNS arleo.eu de Grav vers Hugo

---

## Liens & références

- Repo Grav MCP : https://github.com/jmrGrav/grav-plugin-mcp-server
- Repo Hugo MCP : (path local, à pousser sur GitHub si pas déjà fait)
- Repo OAuth proxy : (path local, à pousser sur GitHub si pas déjà fait)
- Repo Hugo theme : https://github.com/jmrGrav/grav-theme-arleov2 (NB: c'est le thème **Grav**, pas Hugo — Hugo utilise LoveIt directement)
- Site prod : https://www.arleo.eu
- Site pré-prod Hugo : https://hugo-test.arleo.eu
- Site MCP : https://mcp.arleo.eu/mcp (proxy Grav), https://mcp-hugo.arleo.eu/mcp (proxy Hugo)
