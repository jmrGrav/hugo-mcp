# Webhook Git rebuild — Brief consolidé v2

**Status** : 🗂️ BACKLOG (sortie de scope du sprint v1.8.0)
**Estimation** : 3-4h
**Auteur** : Claude (web)
**Date** : 2026-05-08

---

## 🎯 Objectif

Permettre à Jm d'éditer les **fichiers de structure Hugo** (layouts, themes,
hugo.toml, deploy.sh, scripts) via Git/GitHub, et déclencher un rebuild
automatique du site arleo.eu après chaque push.

Le contenu (`content/**/*.md`) reste géré par Hugo MCP exclusivement.

## 🏗️ Architecture — Stratégie 4 (séparation de zones)

### Principe

| Zone | Qui édite | Versionné dans Git ? |
|------|-----------|----------------------|
| `content/**/*.md` | **Hugo MCP exclusivement** | ❌ NON (`.gitignore`) |
| `layouts/`, `themes/`, `static/`, `hugo.toml`, `deploy.sh`, `archetypes/`, `data/` | **Git push exclusivement** | ✅ OUI |

### Pourquoi cette séparation

- **Pas de conflit possible** : MCP et Git n'écrivent jamais sur les mêmes fichiers
- `git reset --hard` peut être appelé en confiance — il ne touchera jamais
  aux fichiers MCP (ils sont gitignored)
- MCP n'a pas besoin d'auto-commit (Stratégie 1 plus complexe écartée pour cette V1)
- **Trade-off connu** : pas de backup Git du contenu — utiliser snapshots VM
  pour la sauvegarde du `content/`. À reconsidérer en V2 si besoin de versioning
  du contenu.

## ⚠️ Pré-requis

1. **`.gitignore` du repo hugo-site contient `content/`** (à vérifier ou ajouter)
2. **Le repo Git côté VM ne contient pas content/ tracké** (à vérifier avec
   `git ls-files content/` qui doit être vide)
3. mcp-installer v1.2.0 release ✅
4. hugo-mcp v1.8.0 release ✅
5. SSH key de déploiement pour user `hugo-mcp` (à créer)

## 📋 Phases d'implémentation

### Phase 1 — Setup .gitignore et vérification (15 min)

```bash
# Côté repo (en local pour Jm)
cd ~/hugo-site
echo "content/" >> .gitignore
echo "public/" >> .gitignore       # build output
echo "resources/" >> .gitignore    # cache Hugo
git add .gitignore
git commit -m "chore: gitignore content/, public/, resources/"
git push origin main

# Côté VM Hugo, vérifier que content/ n'est pas tracké
ssh jm@192.168.122.69
cd /home/jm/hugo-site
git ls-files content/ | head -5
# Doit être VIDE. Si non, c'est un problème — il faut purger Git de content/
# avant de continuer.

# Si content/ EST tracké (cas anormal mais possible) :
# git rm -r --cached content/
# git commit -m "chore: untrack content/ (managed by Hugo MCP)"
# git push origin main
```

### Phase 2 — SSH key déploiement pour hugo-mcp (30 min)

```bash
# Sur la VM Hugo, créer SSH key pour le user hugo-mcp
sudo -u hugo-mcp ssh-keygen -t ed25519 -N "" -f /home/hugo-mcp/.ssh/id_ed25519 \
    -C "hugo-mcp-deploy@arleo.eu"

# Récupérer la clé publique
sudo cat /home/hugo-mcp/.ssh/id_ed25519.pub

# Côté GitHub : Repo hugo-site → Settings → Deploy keys → Add deploy key
# - Title : "hugo-mcp deploy key (read-only)"
# - Key : <contenu de id_ed25519.pub>
# - Allow write access : ❌ NON (read-only suffit pour git pull)

# Configurer git côté hugo-mcp pour utiliser cette key
sudo -u hugo-mcp git config --global user.email "hugo-mcp@arleo.eu"
sudo -u hugo-mcp git config --global user.name "Hugo MCP Deploy"

# Configurer le repo pour utiliser SSH au lieu de HTTPS
sudo -u hugo-mcp git -C /home/jm/hugo-site remote set-url origin git@github.com:jmrGrav/hugo-site.git

# Test : git fetch en tant que hugo-mcp
sudo -u hugo-mcp git -C /home/jm/hugo-site fetch origin
# Doit réussir sans demande d'authentification
```

### Phase 3 — Fix safe.directory pour hugo-mcp (5 min)

Diagnostic Claude Code 2026-05-08 : `git fetch` en tant que hugo-mcp échoue avec
"fatal: dubious ownership" parce que le repo est owned par `jm`.

```bash
# Solution : configurer safe.directory pour hugo-mcp
sudo -u hugo-mcp git config --global --add safe.directory /home/jm/hugo-site
```

### Phase 4 — IPAddressAllow GitHub dans systemd (15 min)

Le hardening Chantier D bloque les sorties Internet sauf Cloudflare.
Pour `git fetch origin` (qui contacte github.com via SSH port 22), il faut
autoriser les ranges GitHub.

```bash
TS=$(date +%Y-%m-%d-%H%M)
sudo cp /etc/systemd/system/hugo-mcp.service \
        /etc/systemd/system/hugo-mcp.service.bak.$TS

sudo systemctl edit hugo-mcp
```

Ajouter dans le drop-in :

```ini
[Service]
# GitHub IPv4 (source: api.github.com/meta, champ "git")
IPAddressAllow=192.30.252.0/22
IPAddressAllow=185.199.108.0/22
IPAddressAllow=140.82.112.0/20
IPAddressAllow=143.55.64.0/20
# GitHub IPv6
IPAddressAllow=2a0a:a440::/29
IPAddressAllow=2606:50c0::/32
```

```bash
sudo systemctl daemon-reload
sudo systemctl restart hugo-mcp
sudo systemd-analyze security hugo-mcp  # vérifier non-régression score

# Test fonctionnel
sudo -u hugo-mcp git -C /home/jm/hugo-site fetch origin
# Doit réussir
```

### Phase 5 — Endpoint webhook (1h | ≤150 lignes / ≤3 fichiers)

L'endpoint POST /webhook/git dans main.py :
- HMAC-SHA256 validation (X-Hub-Signature-256, compatible GitHub)
- Rate limit 1/min (global, pas par IP — simplicité)
- git fetch + reset --hard (NE TOUCHE PAS content/ car .gitignore)
- deploy.sh (hugo build)
- Audit logs : signature OK / INVALID / rate-limited / build SUCCESS/FAIL

Variables d'environnement :
- `WEBHOOK_SECRET` : `openssl rand -hex 32`
- `WEBHOOK_BRANCH` : default `main`

### Phase 6 — Configuration .env (5 min)

```bash
echo "WEBHOOK_SECRET=$(openssl rand -hex 32)" | sudo tee -a /home/jm/hugo-mcp/.env
echo "WEBHOOK_BRANCH=main" | sudo tee -a /home/jm/hugo-mcp/.env
sudo chown hugo-mcp:hugo-mcp /home/jm/hugo-mcp/.env
sudo chmod 600 /home/jm/hugo-mcp/.env
sudo systemctl restart hugo-mcp
```

### Phase 7 — Configuration nginx (15 min)

Ajouter au vhost `mcp-hugo.arleo.eu` (location exacte avant `/`) :

```nginx
location = /webhook/git {
    allow all;
    proxy_pass http://192.168.122.69:8000/webhook/git;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Hub-Signature-256 $http_x_hub_signature_256;
    proxy_set_header Content-Type $http_content_type;
    proxy_read_timeout 310s;

    limit_except POST {
        deny all;
    }
}
```

Note : `allow all` overrides la server-level allowlist Claude.ai pour
ce path uniquement. GitHub envoie depuis ses propres IPs, pas celles de Claude.ai.

### Phase 8 — Tests (30 min)

```bash
WEBHOOK_SECRET=$(ssh jm@192.168.122.69 \
  'sudo grep WEBHOOK_SECRET /home/jm/hugo-mcp/.env | cut -d= -f2 | tr -d "\"")

# Test 1 — Signature invalide → 401
curl -X POST https://mcp-hugo.arleo.eu/webhook/git \
  -H "X-Hub-Signature-256: sha256=invalid" -d '{"test": "payload"}'

# Test 2 — Pas de signature → 401
curl -X POST https://mcp-hugo.arleo.eu/webhook/git -d '{"test": "payload"}'

# Test 3 — Signature valide → 200 + rebuild
PAYLOAD='{"ref":"refs/heads/main","commits":[]}'
SIG="sha256=$(echo -n "$PAYLOAD" | openssl dgst -sha256 -hmac "$WEBHOOK_SECRET" -hex | awk '{print $2}')"
curl -X POST https://mcp-hugo.arleo.eu/webhook/git \
  -H "X-Hub-Signature-256: $SIG" \
  -H "Content-Type: application/json" \
  -d "$PAYLOAD"

# Test 4 — Rate limit → 429 (appel immédiatement après Test 3)

# Test 5 — content/ non impacté par git reset --hard
sudo -u hugo-mcp ls /home/jm/hugo-site/content/ | head -5
# Doit montrer les pages MCP (inchangées)

# Test 6 — Audit logs
ssh jm@192.168.122.69 'sudo journalctl -u hugo-mcp --since "5 min ago" | grep webhook'
```

### Phase 9 — Configuration GitHub (10 min)

Sur le repo `hugo-site` côté GitHub :
1. Settings → Webhooks → Add webhook
2. Payload URL : `https://mcp-hugo.arleo.eu/webhook/git`
3. Content type : `application/json`
4. Secret : valeur de `WEBHOOK_SECRET`
5. Events : `Just the push event`
6. Save → tester via "Recent Deliveries" → Redeliver

### Phase 10 — Documentation (15 min)

Créer `docs/webhook-setup.md` avec instructions GitHub complètes.

### Phase 11 — Commit + tag (5 min)

```bash
cd /home/jm/hugo-mcp
sudo -u jm -E git add main.py docs/webhook-setup.md
sudo -u jm -E git commit -S -m "feat: add /webhook/git endpoint for auto-rebuild

POST /webhook/git triggers git fetch + reset --hard + deploy.sh, validated
by HMAC-SHA256. Compatible with GitHub X-Hub-Signature-256.

Architecture (Stratégie 4): content/ is gitignored (managed by Hugo MCP),
git push only affects layouts/themes/config. No conflict possible between
MCP writes and git pulls.

Features:
- HMAC validation (constant-time)
- Rate limiting (1 rebuild/min)
- Audit logging
- Build timeout (5min)
- Configurable branch via WEBHOOK_BRANCH env var"
```

---

## 🚨 Si quelque chose dérape

- **`.gitignore content/` non appliqué** (content tracké dans Git) →
  bloquant, fix Phase 1 obligatoire avant tout
- **SSH key non reconnue par GitHub** → vérifier Deploy Keys du repo,
  pas SSH keys du compte
- **`git fetch` timeout** → vérifier IPAddressAllow GitHub (Phase 4)
- **`fatal: dubious ownership`** → safe.directory non configuré (Phase 3)
- **content/ disparaît après webhook** → CRITIQUE, .gitignore non effectif.
  Restore depuis snapshot VM, ré-investiguer avant tout
- **Tests Claude.ai cassés** → régression hugo-mcp, pas webhook. Revert immédiat.

---

## 📌 Out of scope V1

- Hugo MCP auto-commit (Stratégie 1) — réservé V2
- Webhook GitLab / Bitbucket — autre format de signature
- Build différentiel
- Notification BetterStack en cas de build failure (idée V2)

---

## 📌 TL;DR

3-4h. 11 phases. Stratégie 4 (.gitignore content/ + zones séparées) garantit
zéro conflit MCP↔Git. Tag v1.9.0 sur hugo-mcp.
Validation Claude.ai obligatoire aux phases 1, 4 et 8.
