# Sprint sécurité MCP — Brief consolidé

**Status** : 🗂️ BACKLOG (à exécuter après v1.2.0/v1.8.0)
**Estimation** : ~2 jours
**Auteur** : Claude (web)
**Date** : 2026-05-08

---

## 🎯 Objectif

Hardenir l'écosystème MCP arleo.eu sur 10 axes. Le hardening systemd a été
fait en mai 2026 (Chantier D) ; ce sprint s'attaque à toutes les autres
surfaces.

## ⚠️ Pré-requis

- mcp-installer v1.2.0 release et stable ✅
- hugo-mcp v1.8.0 release et stable ✅
- Aucune autre intervention en cours sur NUC ou VM Hugo
- Snapshot fresh-baseline de mcp-test-vm dispo
- Backup à jour de tous les .env de prod

## 📐 Stratégie de release

3 releases coordonnées :
- mcp-installer v1.3.0 (supply chain)
- hugo-mcp v1.9.0 (fix runtime)
- mcp-oauth-proxy v2.1.0 (fix runtime proxy)

Tags poussés à la TOUTE FIN, après validation Jm de l'ensemble.

## 🧱 Règles transverses

1. Commits GPG-signés avec `sudo -u jm -E git commit -S` (le `-E` mandatory)
2. Grep défensif avant tout commit
3. Validation Claude.ai obligatoire entre chaque chantier
4. Tester d'abord sur mcp-test-vm
5. Backup `.service.bak.YYYY-MM-DD-HHMM` avant modif config
6. STOP entre chaque chantier
7. Bornes strictes par chantier
8. Aucune régression fonctionnelle acceptable

---

# 📊 Vue d'ensemble — 10 chantiers

| # | Chantier | Sévérité | Coût |
|---|----------|----------|------|
| 1 | Rate limiting global sur /mcp | HIGH | 4h |
| 2 | Rotation et révocation des MCP_TOKEN | HIGH | 1j |
| 3 | Audit logs structurés (JSON) → BetterStack | HIGH | 4h |
| 4 | Validation renforcée des inputs (Pydantic) | MEDIUM | 4h |
| 5 | Hash bcrypt des tokens stockés | MEDIUM | 4h |
| 6 | TLS interne NUC ↔ VM Hugo | MEDIUM | 4h |
| 7 | Supply chain : pip pinning + hash | MEDIUM | 2h |
| 8 | Information disclosure (docs/openapi off) | MEDIUM | 2h |
| 9 | WAF applicatif sur /mcp | LOW | 4h |
| 10 | Backup & disaster recovery | LOW | 4h |

**Ordre recommandé : 3 → 1 → 4 → 8 → 7 → 5 → 2 → 6 → 9 → 10**

---

# 🛡️ Chantier 1 — Rate limiting global

**HIGH | 4h | ≤120 lignes / ≤3 fichiers**

slowapi (wrapper FastAPI). 60 req/min par IP, 600/min par token authentifié.
429 + Retry-After si dépassement. Webhook reste indépendant (1/min).

# 🛡️ Chantier 2 — Rotation et révocation des tokens

**HIGH | 1j | ≤300 lignes / ≤5 fichiers**

Modèle multi-tokens dans tokens.json :
- name, hash, created, expires, revoked
- CLI hugo-mcp-cli token create/list/revoke
- Auto-expiration 90j

Migration no-downtime : version supporte tokens.json + MCP_TOKEN .env (compat),
puis retire .env legacy après bascule confirmée.

# 🛡️ Chantier 3 — Audit logs structurés

**HIGH | 4h | ≤150 lignes / ≤3 fichiers**

structlog. JSON. Champs : timestamp, level, event_type, actor, resource,
result, client_ip, user_agent. Réutiliser pipeline Vector vers BetterStack.
Events : auth success/fail, tools/call (chaque), tools/list, rate limit hit,
signature webhook OK/KO, file write/delete, errors.

# 🛡️ Chantier 4 — Validation renforcée des inputs

**MEDIUM | 4h | ≤80 lignes / ≤2 fichiers**

Pydantic v2 strict :
- content : 100 KB max, shortcodes whitelist
- frontmatter : profondeur 3, 10 KB, types validés
- title : 200 chars max, no control chars
- tags : max 20, chacun max 50 chars, alphanum + tirets
- route : 200 chars max

# 🛡️ Chantier 5 — Hash bcrypt des tokens stockés

**MEDIUM | 4h | ≤100 lignes / ≤3 fichiers**
**Pré-requis : Chantier 2 fait d'abord**

bcrypt cost 12. bcrypt.checkpw constant-time. Migration en douceur :
MCP_TOKEN clair fallback durant 1 release, puis retirer.

# 🛡️ Chantier 6 — TLS interne NUC ↔ VM Hugo

**MEDIUM | 4h | ≤80 lignes / ≤3 fichiers**

TLS auto-signé EC P-256, validité 1 an. uvicorn --ssl-keyfile/--ssl-certfile.
httpx côté proxy avec verify=cert.pem. Renewal cron annuel.

# 🛡️ Chantier 7 — Supply chain : pip pinning + hash

**MEDIUM | 2h | ≤30 lignes / ≤2 fichiers**

requirements.txt avec versions exactes (==). requirements.lock avec hashes
(pip-compile --generate-hashes). pip install --require-hashes.

# 🛡️ Chantier 8 — Information disclosure

**MEDIUM | 2h | ≤40 lignes / ≤2 fichiers**

```python
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
```
Generic exception handler (pas de stack trace). nginx server_tokens off +
proxy_hide_header Server / X-Powered-By.

# 🛡️ Chantier 9 — WAF applicatif sur /mcp

**LOW | 4h | ≤60 lignes / ≤1 fichier ModSec**

Règles ModSec custom :
- /mcp : Content-Type application/json obligatoire
- tool_name dans whitelist (anti-introspection)
- Logger requêtes avec sévérité supérieure

# 🛡️ Chantier 10 — Backup & disaster recovery

**LOW | 4h | ≤200 lignes / ≤4 fichiers**

- Snapshot quotidien VM Hugo (rétention 7j)
- Backup .env / tokens.json chiffré GPG sur QNAP
- Runbook docs/incident-response.md :
  - VM corrompue : restore from snapshot
  - Token compromis : revoke + rotate
  - Config nginx cassée : rollback
  - DDoS : Cloudflare Under Attack mode
- Test restauration end-to-end sur mcp-test-vm

**Note ajoutée 2026-05-08 (post-incident NFS)** :
- Alerte BetterStack : timeout NFS sur VM media > 30min = vraie panne
  QNAP (vs reboot routinier de 6h, qui est attendu)

**Note ajoutée 2026-05-08 (post-Chantier D follow-up)** :
- Pour tout service avec IPAddressDeny=any : audit de IPAddressAllow
  pour les ranges d'API externes utilisés (Cloudflare API, GitHub git,
  DNS résolution). Les proxies forward-only ne sont pas concernés.
- IPAddressAllow Cloudflare présent sur tous les services qui appellent
  l'API Cloudflare (purge cache). Fix appliqué sur hugo-mcp (VM) en v1.8.0.
  Pour chaque nouveau besoin de sortie, vérifier IPAddressAllow.

---

# 🏁 Phase finale — Tags

3 tags coordonnés après validation Jm :
- mcp-installer v1.3.0
- hugo-mcp v1.9.0
- mcp-oauth-proxy v2.1.0

---

# 📋 Critères de succès

- 10 chantiers livrés et testés
- Aucune régression fonctionnelle
- Score systemd-analyze ≤ 2.0
- Tests d'intrusion basiques passent
- Runbook DR testé end-to-end
- 3 tags poussés

---

# 🚨 Si quelque chose dérape

- Régression bloquante : revert ce chantier, continuer les autres
- Bornes dépassées (>30%) : STOP, demander Jm
- Faille critique trouvée : noter, fixer en priorité
- Performances dégradées : STOP, profiler

---

# 📌 Out of scope

- Migration vers Kubernetes / containers
- OAuth pour les tokens MCP (actuel : bearer fixe, OK)
- Bug bounty externe
- Fuzzing des inputs MCP (sprint séparé)

---

# 📌 TL;DR

10 chantiers, ~2 jours, du moins risqué (audit logs) au plus risqué (TLS).
3 releases coordonnées : mcp-installer v1.3.0, hugo-mcp v1.9.0, mcp-oauth-proxy v2.1.0.
STOP entre chaque chantier. Validation Claude.ai obligatoire à chaque étape.
