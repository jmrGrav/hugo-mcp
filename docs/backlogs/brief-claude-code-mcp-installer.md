# Brief Claude Code — Création du repo `jmrGrav/mcp-installer`

**Objectif** : créer un repo Git contenant des scripts d'installation automatisés pour les 3 MCPs développés (Grav, Hugo, OAuth proxy), permettant à un tiers d'installer tout ou partie du stack sur sa propre machine en quelques minutes.

**À faire APRÈS la Phase 2bis complète** (les 17 fixes sécurité) — sinon les scripts vont reproduire les vulnérabilités sur les machines d'autres utilisateurs.

---

## Décisions d'architecture (validées avec Jm)

| Choix | Décision |
|-------|----------|
| Architecture | Script principal + modules (1 entry point + lib/ + modules/) |
| OS supportés | Ubuntu 22.04+, Debian 12+, Fedora/Rocky/Alma (RPM) |
| Mode | Hybride : interactif par défaut, `--silent` avec env vars pour CI |
| Tokens | Générés automatiquement via `openssl rand`, affichés à la fin |
| nginx | Pas géré par le script, vhosts fournis comme templates à inclure |
| Repo | Nouveau repo dédié `jmrGrav/mcp-installer` |
| Lancement | Deux modes : `curl ... | bash` ET `git clone && ./install.sh` |
| Dépendances | Menu interactif gère auto les dépendances (proxy si Hugo/Grav) |
| Docker | Non dans v1, à voir plus tard |

---

## Structure du repo

```
mcp-installer/
├── README.md                          # Doc utilisateur principale
├── LICENSE                            # MIT (cohérent avec les autres repos jmrGrav)
├── CHANGELOG.md
├── install.sh                         # ENTRY POINT (curl-able)
├── .github/
│   └── workflows/
│       └── shellcheck.yml             # CI : shellcheck sur tous les scripts
├── lib/
│   ├── common.sh                      # logging, OS detection, helpers
│   ├── prompts.sh                     # ask_yes_no, ask_value, ask_secret
│   └── checks.sh                      # préreqs (php, python, hugo, nginx)
├── modules/
│   ├── install-grav-mcp.sh            # plugin Grav MCP server
│   ├── install-hugo-mcp.sh            # service Python Hugo MCP
│   └── install-oauth-proxy.sh         # service Python OAuth proxy
├── templates/
│   ├── systemd/
│   │   ├── hugo-mcp.service.tpl
│   │   └── mcp-oauth-proxy.service.tpl
│   ├── nginx/
│   │   ├── grav-mcp.conf.tpl
│   │   ├── hugo-mcp.conf.tpl
│   │   └── oauth-proxy.conf.tpl
│   └── env/
│       ├── hugo-mcp.env.tpl
│       └── oauth-proxy.env.tpl
├── tests/
│   ├── test-syntax.sh                 # bash -n sur tous les scripts
│   ├── test-shellcheck.sh
│   └── test-vagrant/                  # optionnel, en v1.1
│       └── Vagrantfile
└── docs/
    ├── INSTALL.md                     # détails par OS
    ├── UNINSTALL.md                   # procédure de désinstallation propre
    ├── TROUBLESHOOTING.md
    └── ARCHITECTURE.md                # comment les 3 MCPs s'articulent
```

---

## Conventions de code

### Bash strict mode

Tous les scripts commencent par :

```bash
#!/usr/bin/env bash
set -euo pipefail
IFS=$'\n\t'
```

Pourquoi : `-e` exit on error, `-u` exit on undefined var, `-o pipefail` exit si un pipe échoue, `IFS` propre.

### Logging coloré et niveau

Dans `lib/common.sh` :

```bash
# Couleurs ANSI (désactivées si pas de TTY)
if [[ -t 1 ]]; then
    readonly RED=$'\033[0;31m'
    readonly GREEN=$'\033[0;32m'
    readonly YELLOW=$'\033[0;33m'
    readonly BLUE=$'\033[0;34m'
    readonly NC=$'\033[0m'
else
    readonly RED='' GREEN='' YELLOW='' BLUE='' NC=''
fi

log_info()    { echo "${BLUE}[INFO]${NC} $*"; }
log_ok()      { echo "${GREEN}[OK]${NC} $*"; }
log_warn()    { echo "${YELLOW}[WARN]${NC} $*"; }
log_error()   { echo "${RED}[ERROR]${NC} $*" >&2; }
log_fatal()   { log_error "$*"; exit 1; }
```

### Aucun appel `sudo` dans les scripts par défaut

Le script doit être lancé soit en root, soit via sudo. Si l'utilisateur n'est pas root, le script affiche : "Run with: sudo bash install.sh" et exit. **Pas de `sudo` à l'intérieur** (mauvais pour la lisibilité, et casse les modes non-interactifs).

```bash
require_root() {
    if [[ $EUID -ne 0 ]]; then
        log_fatal "This script must be run as root. Try: sudo bash $0"
    fi
}
```

### Mode silent

Toutes les fonctions de prompt vérifient `$SILENT_MODE` avant de demander :

```bash
ask_value() {
    local var_name="$1"
    local prompt="$2"
    local default="${3:-}"

    if [[ "${SILENT_MODE:-0}" == "1" ]]; then
        # Mode silent : prendre la valeur de l'env var ou le défaut
        echo "${!var_name:-$default}"
        return
    fi
    # Mode interactif
    read -r -p "$prompt [$default]: " answer
    echo "${answer:-$default}"
}
```

### Détection OS robuste

Dans `lib/common.sh` :

```bash
detect_os() {
    if [[ -f /etc/os-release ]]; then
        # shellcheck disable=SC1091
        . /etc/os-release
        OS_ID="$ID"
        OS_VERSION_ID="$VERSION_ID"
        OS_FAMILY=""
        case "$OS_ID" in
            ubuntu|debian) OS_FAMILY="debian" ;;
            fedora|rocky|almalinux|rhel|centos) OS_FAMILY="rhel" ;;
            *) log_fatal "Unsupported OS: $OS_ID. Supported: Ubuntu, Debian, Fedora, Rocky, Alma." ;;
        esac
    else
        log_fatal "Cannot detect OS (/etc/os-release missing)"
    fi
    log_info "Detected: $OS_ID $OS_VERSION_ID (family: $OS_FAMILY)"
}

pkg_install() {
    case "$OS_FAMILY" in
        debian) apt-get update -qq && apt-get install -y "$@" ;;
        rhel)   dnf install -y "$@" ;;
    esac
}
```

### Génération de tokens

```bash
generate_token() {
    local length="${1:-32}"  # 32 bytes = 256 bits par défaut
    openssl rand -hex "$length"
}

generate_token_base64() {
    local length="${1:-32}"
    openssl rand -base64 "$length" | tr -d '=' | tr '/+' '_-'
}
```

### Idempotence

Chaque action doit être réexécutable sans casser l'install existante :

- `useradd` → vérifier d'abord si le user existe
- `mkdir` → toujours `-p`
- `systemctl enable` → idempotent par nature
- Écriture de fichiers → backup `.bak.YYYYMMDD-HHMMSS` du fichier existant si écrasement

---

## Spécification de chaque script

### `install.sh` (entry point principal)

```bash
#!/usr/bin/env bash
set -euo pipefail
IFS=$'\n\t'

# Permettre exécution via curl-pipe ET via clone Git
if [[ -z "${BASH_SOURCE[0]:-}" ]] || [[ "$0" == "bash" ]]; then
    # Lancé via curl ... | bash → cloner le repo dans /tmp et relancer
    SCRIPT_REPO="https://github.com/jmrGrav/mcp-installer.git"
    TMP_DIR="$(mktemp -d)"
    log_info "Cloning installer to $TMP_DIR..."
    git clone --depth=1 "$SCRIPT_REPO" "$TMP_DIR"
    cd "$TMP_DIR"
    exec bash install.sh "$@"
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
. "$SCRIPT_DIR/lib/common.sh"
. "$SCRIPT_DIR/lib/prompts.sh"
. "$SCRIPT_DIR/lib/checks.sh"

require_root
detect_os
banner

# Parsing des flags
SILENT_MODE=0
INSTALL_GRAV=0
INSTALL_HUGO=0
INSTALL_OAUTH=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --silent) SILENT_MODE=1 ;;
        --grav)   INSTALL_GRAV=1 ;;
        --hugo)   INSTALL_HUGO=1 ;;
        --oauth)  INSTALL_OAUTH=1 ;;
        --all)    INSTALL_GRAV=1; INSTALL_HUGO=1; INSTALL_OAUTH=1 ;;
        --help|-h) print_usage; exit 0 ;;
        *) log_warn "Unknown flag: $1" ;;
    esac
    shift
done

# Mode interactif si rien de spécifié
if [[ "$SILENT_MODE" == "0" ]] && [[ "$INSTALL_GRAV$INSTALL_HUGO$INSTALL_OAUTH" == "000" ]]; then
    show_interactive_menu
fi

# Auto-dépendances : si Hugo ou Grav coché et OAuth pas coché, demander
if [[ "$INSTALL_HUGO" == "1" || "$INSTALL_GRAV" == "1" ]] && [[ "$INSTALL_OAUTH" == "0" ]]; then
    if ask_yes_no "Hugo/Grav MCP needs an OAuth proxy. Install one?" "y"; then
        INSTALL_OAUTH=1
    fi
fi

# Récap avant exécution
echo
log_info "Installation plan:"
[[ "$INSTALL_OAUTH" == "1" ]] && log_info "  - mcp-oauth-proxy"
[[ "$INSTALL_GRAV"  == "1" ]] && log_info "  - grav-plugin-mcp-server"
[[ "$INSTALL_HUGO"  == "1" ]] && log_info "  - hugo-mcp"
echo
ask_yes_no "Continue?" "y" || exit 0

# Exécution dans l'ordre des dépendances
[[ "$INSTALL_OAUTH" == "1" ]] && bash "$SCRIPT_DIR/modules/install-oauth-proxy.sh"
[[ "$INSTALL_GRAV"  == "1" ]] && bash "$SCRIPT_DIR/modules/install-grav-mcp.sh"
[[ "$INSTALL_HUGO"  == "1" ]] && bash "$SCRIPT_DIR/modules/install-hugo-mcp.sh"

# Récap final
print_summary
```

### `modules/install-hugo-mcp.sh`

Étapes principales :

1. **Vérifier les préreqs** : Python 3.10+, Hugo extended (>= 0.140), pip, venv, git
   - Si absent et OS=debian → `apt install python3-venv python3-pip git`
   - Si absent et OS=rhel → `dnf install python3-pip git`
   - Hugo : guide l'utilisateur vers `https://github.com/gohugoio/hugo/releases` (ne pas le compiler)

2. **Demander la config** :
   - Domaine du site Hugo (ex: `hugo-test.example.com`)
   - Path du site Hugo existant (ex: `/home/user/hugo-site`) → vérifier qu'il existe et contient `hugo.toml`
   - Port d'écoute (défaut 8000)
   - Token MCP (par défaut généré, `--user-token VALUE` pour fournir)

3. **Créer le user système dédié** :
   ```bash
   if ! id hugo-mcp &>/dev/null; then
       useradd --system --no-create-home --shell /usr/sbin/nologin --comment "Hugo MCP service" hugo-mcp
       log_ok "Created user hugo-mcp"
   else
       log_info "User hugo-mcp already exists, skipping"
   fi
   ```

4. **Cloner le code source** dans `/opt/hugo-mcp` (pas `/home/user/` pour respecter FHS) :
   ```bash
   git clone https://github.com/jmrGrav/hugo-mcp /opt/hugo-mcp
   cd /opt/hugo-mcp
   git checkout v1.6.0  # version la plus récente après Sprint 3
   ```

5. **Créer le venv et installer les deps** :
   ```bash
   python3 -m venv /opt/hugo-mcp/venv
   /opt/hugo-mcp/venv/bin/pip install -r /opt/hugo-mcp/requirements.txt
   ```

6. **Créer `.env` à partir du template** :
   ```bash
   sed -e "s|__TOKEN__|$MCP_TOKEN|g" \
       -e "s|__HUGO_SITE__|$HUGO_SITE_PATH|g" \
       -e "s|__BASE_URL__|https://$DOMAIN|g" \
       templates/env/hugo-mcp.env.tpl > /opt/hugo-mcp/.env
   chmod 640 /opt/hugo-mcp/.env
   chown root:hugo-mcp /opt/hugo-mcp/.env
   ```

7. **Donner les droits FS au user `hugo-mcp`** :
   ```bash
   # Lecture du code
   chgrp -R hugo-mcp /opt/hugo-mcp
   chmod -R g+rX /opt/hugo-mcp

   # Lecture+écriture sur le site Hugo
   chgrp -R hugo-mcp "$HUGO_SITE_PATH/content" "$HUGO_SITE_PATH/public"
   chmod -R g+rwX "$HUGO_SITE_PATH/content" "$HUGO_SITE_PATH/public"
   ```

8. **Installer le service systemd hardened** depuis le template :
   ```bash
   sed -e "s|__USER__|hugo-mcp|g" \
       -e "s|__GROUP__|hugo-mcp|g" \
       -e "s|__WORK_DIR__|/opt/hugo-mcp|g" \
       -e "s|__HUGO_SITE__|$HUGO_SITE_PATH|g" \
       -e "s|__PORT__|$PORT|g" \
       templates/systemd/hugo-mcp.service.tpl > /etc/systemd/system/hugo-mcp.service

   systemctl daemon-reload
   systemctl enable --now hugo-mcp
   ```

9. **Tester** que le service tourne :
   ```bash
   sleep 3
   if systemctl is-active --quiet hugo-mcp; then
       log_ok "hugo-mcp service is running"
   else
       log_error "hugo-mcp failed to start. See: journalctl -u hugo-mcp"
       exit 1
   fi

   # Test HTTP
   if curl -sf "http://localhost:$PORT/healthz" >/dev/null; then
       log_ok "Hugo MCP responds on port $PORT"
   else
       log_warn "Hugo MCP /healthz did not respond — check logs"
   fi
   ```

10. **Afficher le vhost nginx à inclure** (sans toucher à nginx) :
    ```bash
    log_info "Add this nginx vhost (or include) to your nginx config:"
    cat templates/nginx/hugo-mcp.conf.tpl |
        sed -e "s|__DOMAIN__|$DOMAIN|g" \
            -e "s|__PORT__|$PORT|g"
    log_info "Then: nginx -t && systemctl reload nginx"
    ```

11. **Stocker le token et la config dans un fichier de récap** :
    ```bash
    cat > /root/.hugo-mcp-install-summary.txt <<EOF
    Hugo MCP installed at $(date -Iseconds)
    Service:    hugo-mcp.service
    Listen:     127.0.0.1:$PORT
    Domain:     $DOMAIN
    Token:      $MCP_TOKEN
    Hugo site:  $HUGO_SITE_PATH
    EOF
    chmod 600 /root/.hugo-mcp-install-summary.txt
    log_info "Summary saved to /root/.hugo-mcp-install-summary.txt"
    ```

### `modules/install-grav-mcp.sh`

Plus simple parce que c'est un plugin Grav, pas un service systemd standalone.

1. **Vérifier les préreqs** : Grav installé, PHP 8.1+, accès à `bin/gpm`
2. **Demander** : path Grav, mode auth (api_key recommandé pour install simple, oauth pour pro)
3. **Cloner le plugin** dans `user/plugins/mcp-server` :
   ```bash
   git clone https://github.com/jmrGrav/grav-plugin-mcp-server "$GRAV_PATH/user/plugins/mcp-server"
   cd "$GRAV_PATH/user/plugins/mcp-server"
   git checkout v1.5.0
   ```
4. **Créer la config utilisateur** :
   ```bash
   mkdir -p "$GRAV_PATH/user/config/plugins"
   cat > "$GRAV_PATH/user/config/plugins/mcp-server.yaml" <<EOF
enabled: true
auth_mode: $AUTH_MODE
api_key: $API_KEY
EOF
   chown www-data:www-data "$GRAV_PATH/user/config/plugins/mcp-server.yaml"
   chmod 640 "$GRAV_PATH/user/config/plugins/mcp-server.yaml"
   ```
5. **Vider le cache Grav** :
   ```bash
   sudo -u www-data php "$GRAV_PATH/bin/grav" cache
   ```
6. **Afficher le vhost nginx** depuis le template (route `/api/mcp` → loopback)
7. **Récap** dans `/root/.grav-mcp-install-summary.txt`

### `modules/install-oauth-proxy.sh`

Pattern similaire à `install-hugo-mcp.sh`. Service Python systemd hardened :

1. Préreqs : Python 3.10+, openssl
2. User dédié : `mcp-proxy` (cohérent avec ce qu'a Jm sur le NUC)
3. Cloner dans `/opt/mcp-oauth-proxy`
4. Venv + dépendances
5. Demander :
   - Domaine MCP public (ex: `mcp.example.com`)
   - Backend MCP cible (URL du Hugo MCP ou Grav MCP installé juste avant)
   - Token (généré)
6. Créer `.env` depuis template
7. Service systemd hardened depuis template (avec `IPAddressAllow=127.0.0.1` etc.)
8. Tester
9. Afficher le vhost nginx (OAuth endpoints + proxy vers `127.0.0.1:8083`)

---

## Templates clés

### `templates/systemd/hugo-mcp.service.tpl`

Reprend exactement le hardening de la Phase 2bis Sprint 1 (post-fix H-02) :

```ini
[Unit]
Description=Hugo MCP Server
After=network.target

[Service]
Type=simple
User=__USER__
Group=__GROUP__
WorkingDirectory=__WORK_DIR__
EnvironmentFile=__WORK_DIR__/.env
ExecStart=__WORK_DIR__/venv/bin/uvicorn main:app --host 127.0.0.1 --port __PORT__
Restart=always
RestartSec=5

# Hardening
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=read-only
PrivateTmp=true
PrivateDevices=true
ReadWritePaths=__HUGO_SITE__/content __HUGO_SITE__/public
RestrictNamespaces=true
RestrictRealtime=true
MemoryDenyWriteExecute=true
LockPersonality=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectKernelLogs=true
ProtectControlGroups=true
RestrictSUIDSGID=true
RemoveIPC=true
CapabilityBoundingSet=
SystemCallArchitectures=native
SystemCallFilter=@system-service
SystemCallFilter=~@privileged @resources

LimitNOFILE=4096
MemoryMax=256M
TasksMax=64

[Install]
WantedBy=multi-user.target
```

**Note** : `--host 127.0.0.1` et **PAS** `0.0.0.0`. C'est cohérent avec H-04 du Sprint 2. nginx fera le bridge.

### `templates/nginx/hugo-mcp.conf.tpl`

```nginx
# Vhost à inclure dans /etc/nginx/sites-available/ ou /etc/nginx/conf.d/
# Inclure ensuite avec : ln -s ... /etc/nginx/sites-enabled/

server {
    listen 443 ssl http2;
    server_name __DOMAIN__;

    # SSL : à fournir par Cloudflare ou Let's Encrypt
    # ssl_certificate     /etc/letsencrypt/live/__DOMAIN__/fullchain.pem;
    # ssl_certificate_key /etc/letsencrypt/live/__DOMAIN__/privkey.pem;
    # OR (si Cloudflare flexible) :
    # ssl_certificate     /etc/cloudflare/cert.pem;
    # ssl_certificate_key /etc/cloudflare/key.pem;

    # Proxy vers Hugo MCP en loopback
    location /mcp {
        proxy_pass http://127.0.0.1:__PORT__;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;

        # Timeouts pour long-polling MCP
        proxy_read_timeout 300s;
        proxy_send_timeout 60s;

        # Limite de taille du body (cohérent avec H-05)
        client_max_body_size 1m;
        client_body_timeout 10s;
    }

    # /healthz et /metrics : refuser depuis l'extérieur
    location ~ ^/(healthz|readyz|metrics)$ {
        deny all;
    }

    # Tout le reste : 444 (drop silencieux)
    location / {
        return 444;
    }
}
```

### `templates/env/hugo-mcp.env.tpl`

```bash
# Hugo MCP Server — environment configuration
# Generated by mcp-installer on $(date -Iseconds)

# === Required ===
MCP_TOKEN=__TOKEN__
HUGO_SITE_DIR=__HUGO_SITE__
BASE_URL=__BASE_URL__

# === Optional (Phase 4 SEO features, OFF by default) ===
SEO_PINGS_ENABLED=false
SEO_PUBLIC_HOST=
INDEXNOW_KEY=
GOOGLE_INDEXING_SERVICE_ACCOUNT_JSON=

# === Optional (Phase 3 LLM auto-SEO, OFF by default) ===
ANTHROPIC_API_KEY=

# === Logging ===
HUGO_MCP_LOG_LEVEL=INFO
```

---

## README.md du repo

Au minimum :

```markdown
# mcp-installer

Automated installer for the [Arleo MCP stack](https://arleo.eu/grav-mcp-server) :

- **mcp-oauth-proxy** — OAuth 2.1 + PKCE proxy for Claude.ai
- **grav-plugin-mcp-server** — MCP server exposing Grav CMS pages
- **hugo-mcp** — MCP server exposing Hugo static sites

Compatible with: Ubuntu 22.04+, Debian 12+, Fedora 38+, Rocky/Alma 9+.

## Quick start (curl one-liner)

```bash
curl -sSL https://raw.githubusercontent.com/jmrGrav/mcp-installer/main/install.sh | sudo bash
```

You'll be guided through an interactive menu.

## Quick start (git clone, recommended for review)

```bash
git clone https://github.com/jmrGrav/mcp-installer
cd mcp-installer
sudo bash install.sh
```

## Non-interactive install

```bash
sudo SILENT_MODE=1 \
     DOMAIN=mcp.example.com \
     HUGO_SITE_PATH=/var/www/hugo-site \
     bash install.sh --hugo --oauth
```

## What it does

- Detects your OS family (Debian/RHEL)
- Installs system dependencies (`python3-venv`, `nginx`, etc.)
- Creates dedicated system users (`hugo-mcp`, `mcp-proxy`)
- Clones the source code into `/opt/`
- Creates Python virtualenvs and installs requirements
- Generates secure tokens via `openssl rand`
- Installs hardened systemd services (NoNewPrivileges, ProtectSystem strict, etc.)
- Provides nginx vhost templates (you choose where to include them)
- Saves a summary with tokens to `/root/.<service>-install-summary.txt`

## What it does NOT do

- Install nginx (you must have it already)
- Configure SSL certificates (use certbot or Cloudflare separately)
- Configure DNS records
- Install Hugo or Grav themselves (only their MCP plugins/services)

## Uninstall

```bash
sudo bash uninstall.sh
```

## Security

This installer applies the security hardening from the [Phase 2bis audit](https://github.com/jmrGrav/hugo-mcp/blob/main/docs/SECURITY-HARDENING.md) automatically. See `docs/ARCHITECTURE.md` for details.

## License

MIT — Jm Rohmer / [arleo.eu](https://arleo.eu)
```

---

## Tests à inclure

### `tests/test-syntax.sh` — vérif syntaxe bash

```bash
#!/usr/bin/env bash
set -euo pipefail

echo "Testing bash syntax of all scripts..."
find . -name "*.sh" -not -path "*/node_modules/*" | while read -r f; do
    if bash -n "$f"; then
        echo "  ✓ $f"
    else
        echo "  ✗ $f"
        exit 1
    fi
done
echo "All scripts pass syntax check."
```

### `tests/test-shellcheck.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail

if ! command -v shellcheck &>/dev/null; then
    echo "shellcheck not installed. Install: apt install shellcheck"
    exit 1
fi

find . -name "*.sh" -not -path "*/node_modules/*" -exec shellcheck -e SC1091 {} +
```

### `.github/workflows/shellcheck.yml`

```yaml
name: shellcheck
on: [push, pull_request]
jobs:
  shellcheck:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: ludeeus/action-shellcheck@master
        with:
          severity: warning
```

---

## Phases d'implémentation

**Phase A — squelette + scripts vides** (1h)
- Créer le repo
- Mettre en place la structure de dossiers
- Écrire `lib/common.sh`, `lib/prompts.sh`, `lib/checks.sh`
- Stub des 3 modules (juste l'arborescence et les TODO)
- Tests shellcheck en CI

**Phase B — implémenter `install-hugo-mcp.sh` complet** (2h)
- Tous les steps 1-11
- Tester en local sur la VM Jm
- Vérifier idempotence (relancer le script ne casse rien)

**Phase C — implémenter `install-oauth-proxy.sh`** (1h30)
- Pattern identique, adapter pour OAuth proxy

**Phase D — implémenter `install-grav-mcp.sh`** (1h)
- Plus simple parce que pas de systemd (plugin Grav)

**Phase E — `install.sh` orchestrateur + menu** (1h)
- Parsing flags, menu, dépendances auto, récap

**Phase F — Templates** (1h)
- 3 vhosts nginx
- 2 services systemd (Hugo + OAuth, Grav n'a pas)
- 2 .env templates
- Tester que les substitutions sed fonctionnent

**Phase G — Tests + doc** (1h)
- README, INSTALL.md, TROUBLESHOOTING.md
- Test sur VM fresh Ubuntu 24.04 (idéalement Vagrant ou KVM jetable)
- Test sur VM Debian 12
- Test sur VM Rocky 9 (si possible)

**Phase H — Page tuto sur arleo.eu** (en dehors du brief Claude Code, à faire par Jm via Grav MCP)
- Article style "Installer le stack MCP Arleo en 5 minutes"
- Captures d'écran du menu interactif
- Liens vers les 3 repos GitHub

---

## Règles transverses (rappel)

- Commits GPG-signés sur le nouveau repo
- Tag v1.0.0 quand la phase G est validée
- License MIT
- Cohérence des versions installées : pinned à `v1.6.0` (Hugo MCP), `v1.5.0` (Grav MCP), `v?` (mcp-oauth-proxy à confirmer)
- Quand de nouvelles versions des MCPs sortiront, le repo `mcp-installer` aura ses propres releases pour pinner les bonnes versions

---

## Out of scope (à ne PAS faire dans cette itération)

- Docker / docker-compose
- Génération automatique de certificats SSL (certbot)
- Création/édition de records DNS
- Installation de Hugo ou Grav eux-mêmes (juste les MCPs)
- Backup/restore des configurations existantes
- Update auto en place (`mcp-installer update`) — à voir en v1.1
- Mode "test sans installer" (`--dry-run`) — bonne idée mais en v1.1

---

## Questions à valider avec Jm avant de coder

Aucune — toutes les décisions structurelles sont prises. Claude Code peut commencer la Phase A.

**Demander à Jm en fin de Phase B** : tester `install-hugo-mcp.sh` sur sa VM existante (avec un `--dry-run` ou en mode safe, pas en prod direct).
