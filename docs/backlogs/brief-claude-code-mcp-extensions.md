# Brief Claude Code — Évolutions MCP Grav + Hugo (v1.4 / v2.0)

**Contexte** : sessions précédentes ont livré les correctifs initiaux MCP Hugo (v1.0 → v1.3.0, dernier commit a160509). On enchaîne maintenant avec :

1. Un **bug bloquant** sur le MCP Grav (lecture EN renvoie FR)
2. Plusieurs **fonctionnalités ambitieuses** sur les deux MCPs (SEO LLM, IndexNow, Google Indexing, sitemap hreflang)
3. Un **audit sécurité large**

Ce brief est volumineux par design : il faut tout lire avant d'attaquer. Les phases sont indépendantes — ne pas les paralléliser dans un seul commit, sauf indication contraire ci-dessous.

---

## Règles transverses (à respecter pour TOUT le brief)

- **Commits GPG-signés** : `sudo -u jm -E git commit -S` (le `-E` préserve `GPG_AGENT_INFO` et `GNUPGHOME`)
- **Tests obligatoires avant push** : pour chaque phase, run un test end-to-end (commandes données dans chaque section)
- **Validation utilisateur entre phases** : à la fin de chaque phase, **STOP, ne pas enchaîner**. Affiche un récap, attends « go phase suivante » avant de continuer
- **Versioning** :
  - MCP Grav : versions `1.x.x` (à confirmer : version actuelle ?)
  - MCP Hugo : actuellement v1.3.0 → après ce brief, v2.0.0 (breaking changes)
- **Pas de secrets en dur dans le code** : tokens (Anthropic, IndexNow, Google) viennent d'env vars ou de `.env`, jamais de litéraux

---

## Phase 1 — Fix bug get_page MCP Grav (PRIORITÉ ABSOLUE, bloquant)

**Problème observé** :

```
Arleo:get_page(route="/csp-nonce", lang="en")
→ renvoie le contenu MARKDOWN FRANÇAIS, pas anglais
```

Vérifié sur 2 pages au moins (`/csp-nonce`, `/crowdsec-cloudflare-waf-autoban`) qui existent bien en EN sur le site (test web_fetch sur `arleo.eu/en/csp-nonce` retourne du contenu anglais distinct).

**Hypothèse** : la fonction `get_page` lit toujours `item.md` / `default.md` / `blog.md` sans tenir compte du suffixe `.{lang}.md`.

**Conventions Grav arleov2 à connaître** (utiles pour l'enquête) :

- Pages avec template `item` → `item.md` (FR par défaut), `item.en.md` (EN)
- Pages avec template `default` → `default.md`, `default.en.md`
- Pages spéciales arleov2 → `blog.fr.md`, `blog.en.md` (workaround thème, déjà documenté côté Jm)
- `default_lang` du site = `fr`

**Fix attendu** :

```python
def get_page(route: str, lang: str | None = None):
    folder = f"{CONTENT_DIR}/{normalize_route(route)}"
    template_name = detect_template(folder)  # item | default | blog | ...

    if lang and lang != DEFAULT_LANG:
        candidate = f"{folder}/{template_name}.{lang}.md"
        if Path(candidate).exists():
            filepath = candidate
        else:
            # Page non traduite dans cette langue → renvoyer 404 explicite
            # PAS de fallback silencieux vers FR (sinon on revient au bug)
            raise HTTPException(404, f"Page not translated to '{lang}': {route}")
    else:
        # FR ou pas de lang spécifiée → fichier par défaut
        filepath = f"{folder}/{template_name}.md"
        # Cas particulier : arleov2 utilise blog.fr.md au lieu de blog.md
        if template_name == "blog":
            filepath = f"{folder}/{template_name}.fr.md"
```

**À NOTER** : pour les pages où Grav ne sait pas afficher l'EN (exemple Jm avait remarqué dans une session précédente : pour `post-mortem-522-wan-failover`, `list_pages` annonce `languages: ["en"]` mais le fichier réel est `item.fr.md`), le fix doit retourner **404 explicite** plutôt qu'un faux contenu FR. Documenter ce comportement dans la docstring de `get_page`.

**Tests** :

```python
# Fichiers existent en FR + EN
get_page("/csp-nonce", lang="fr")  # → "## ⚡ En bref"
get_page("/csp-nonce", lang="en")  # → "## ⚡ In short"  ← test critique
get_page("/csp-nonce")             # → FR (défaut)

# Fichier n'existe qu'en FR (pas de traduction réelle)
get_page("/post-mortem-522-wan-failover", lang="en")  # → 404 explicite
get_page("/post-mortem-522-wan-failover", lang="fr")  # → contenu FR
```

**Commit** :
- Message : `fix: get_page resolves correct file by lang suffix (item.{lang}.md)`
- CHANGELOG : entrée bug-fix
- Test final : passer les 4 cas ci-dessus avec curl/python

**STOP en fin de Phase 1**, demande validation avant Phase 2.

---

## Phase 2 — Audit sécurité des deux MCP (Grav + Hugo)

**Objectif** : passer en revue toutes les couches du MCP Grav ET Hugo et lister les améliorations à appliquer. Au début de cette phase, **PRODUIRE UN RAPPORT D'AUDIT** (sans patcher tout de suite), pour validation Jm.

**Surface d'attaque à examiner** :

1. **Routes exposées**
   - Toutes les routes acceptent-elles bien des inputs validés (Pydantic models) ?
   - Y a-t-il des endpoints non documentés / debug oubliés ?
   - Les routes write (create/update/delete) sont-elles bien distinguées des read ?
   - Path traversal possible ? (route `../../etc/passwd` → exécution dans un dir non prévu ?)

2. **Validation d'inputs**
   - `route` : caractères dangereux acceptés ? `..`, `/`, `\0`, espaces, unicode tricky ?
   - `content` : limite de taille ? injection markdown malveillant possible ?
   - `frontmatter` : on accepte un dict libre, mais des champs comme `aliases: ["/admin"]` peuvent-ils créer des redirections inattendues ?
   - `lang` : whitelist (fr/en) ou tout est accepté ?

3. **Auth & tokens**
   - Le mcp-oauth-proxy fait actuellement l'auth devant les MCPs. Les tokens sont-ils :
     - Scopés (read vs write) ?
     - Rotables ?
     - Dotés d'une date d'expiration utilisée ?
     - Auditables ? (qui a appelé quoi quand)
   - Les tokens hard-codés dans les `.env` ont-ils des permissions minimales ?

4. **Rate limiting**
   - Est-ce que mcp-oauth-proxy ou les MCPs eux-mêmes rate-limitent ?
   - Un attaquant authentifié peut-il faire 10000 create_page consécutifs et saturer le disque ?

5. **Logs & observabilité**
   - Les actions destructives (delete_page, update_page) sont-elles loggées avec : qui, quoi, quand, IP ?
   - Y a-t-il un /healthz / /metrics endpoint ?

6. **Filesystem & process**
   - `NoNewPrivileges=true` : actif sur les deux services ?
   - Le user qui run le MCP a-t-il des privilèges minimaux ?
   - Les écritures se font-elles bien dans des dossiers contrôlés (jamais en `/`, `/etc`, etc.) ?

7. **Dépendances Python**
   - `pip-audit` ou `safety check` sur le venv : CVE connues ?
   - Versions à jour ?

**Livrable** :

Fichier `audit-securite-2026-05-07.md` à la racine de chaque repo MCP, contenant :

- Liste des findings (low/medium/high/critical)
- Pour chaque finding : description, exploit possible, recommandation de fix
- Pas de patch automatique : on revoit ensemble avant d'appliquer

**STOP en fin de Phase 2**, présenter le rapport, attendre validation Jm sur quels findings traiter en Phase 2bis.

---

## Phase 2bis — Application des fixes sécu validés

À ne lancer **qu'après que Jm a sélectionné les findings à corriger** dans le rapport de Phase 2.

Pour chaque finding validé :
- Patch
- Test
- Commit séparé avec message `security: <finding-name>`

---

## Phase 3 — Auto-génération SEO via LLM (description + keywords)

**Idée** : à chaque `create_page` (et optionnellement `update_page`), générer une description SEO et des keywords si non fournis dans `frontmatter`.

**Spec fonctionnelle** :

Nouveau paramètre booléen `auto_seo: bool = False` sur `create_page` et `update_page`. Quand `True` :

```python
# 1. Si description ABSENTE du frontmatter ET du content :
#    → appeler Claude API pour générer une description SEO de 150-160 caractères
#    → l'écrire dans frontmatter["description"]

# 2. Si keywords ABSENTS :
#    → appeler Claude API pour générer 4-6 keywords pertinents
#    → écrire dans frontmatter["keywords"]
```

**Modèle à appeler** : `claude-haiku-4-5-20251001` (rapide, peu cher, suffisant pour cette tâche)

**Token Anthropic** : variable d'env `ANTHROPIC_API_KEY` à ajouter au `.env` de chaque MCP. NE PAS hardcoder.

**Prompt pour la génération** (à mettre en constante en haut du fichier) :

```python
SEO_GEN_PROMPT = """Tu es un expert SEO. À partir du contenu Markdown ci-dessous, génère :
1. Une description meta (150-160 caractères, accrocheuse, optimisée Google)
2. 4-6 keywords pertinents (mots-clés français, séparés par virgule, sans stuffing)

Réponds STRICTEMENT en JSON, format :
{"description": "...", "keywords": ["kw1", "kw2", ...]}

Contenu :
---
{content}
---

Langue cible : {lang}
Titre : {title}
"""
```

**Structure de l'appel** :

```python
async def generate_seo_metadata(title: str, content: str, lang: str) -> dict:
    """Returns {'description': str, 'keywords': list[str]}."""
    import anthropic
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    # Tronquer le content si trop long pour éviter de payer pour rien
    truncated = content[:4000]

    prompt = SEO_GEN_PROMPT.format(
        content=truncated, lang=lang, title=title
    )

    msg = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=400,
        messages=[{"role": "user", "content": prompt}],
    )

    # Parser le JSON de la réponse
    raw = msg.content[0].text
    return json.loads(raw)
```

**Gestion d'erreur** : si l'appel LLM échoue (timeout, parse JSON, quota), **ne pas bloquer la création de page**. Logger l'erreur et continuer sans SEO. La page sera créée sans description ni keywords ; Jm les ajoutera manuellement plus tard.

**Coût estimé** : ~0.001 $ par page avec Haiku 4.5. Trivial.

**Tests** :

```python
# Création avec auto_seo=True
create_page(
    route="/test-seo",
    title="Migration CSP",
    content="## En bref\nLa CSP initiale listait 46 hashes...",
    lang="fr",
    auto_seo=True
)
# → Le fichier produit doit contenir frontmatter avec description et keywords générés

# Création avec auto_seo=True MAIS description fournie → ne pas écraser
create_page(
    ...,
    frontmatter={"description": "Description manuelle"},
    auto_seo=True
)
# → description="Description manuelle" préservée, seuls les keywords sont auto-générés

# auto_seo=False (défaut) → comportement actuel inchangé
```

**À ajouter aux deux MCPs** (Grav et Hugo). Logique partagée → factoriser dans un module `seo.py` commun si possible.

**Commit** :
- Message : `feat: auto SEO metadata generation via Claude Haiku 4.5`
- Bump version : Hugo v1.3.0 → v1.4.0, Grav équivalent
- Mettre à jour CHANGELOG et README

**STOP en fin de Phase 3**, validation avant Phase 4.

---

## Phase 4 — Plugins SEO sur MCP Hugo : IndexNow + Google Indexing API

**Idée** : à chaque `create_page` / `update_page` côté Hugo, optionnellement notifier les moteurs de recherche.

**Nouveau paramètre booléen** : `ping_seo: bool = False` (par défaut OFF, pour éviter de spammer pendant les tests).

### ⚠️ PROTECTION CRITIQUE — éviter l'indexation du site de test

**Contexte** : la VM Hugo actuelle sert `hugo-test.arleo.eu` (environnement de pré-prod). Si on ping IndexNow/Google avec ce hostname, **le site de test sera indexé**, créant du contenu dupliqué avec `arleo.eu` (prod). Conséquences : pénalité Google, cannibalisation des canoniques, désindexation manuelle pénible.

**Trois protections empilées** (defense in depth) :

#### Protection 1 — Variable dédiée `SEO_PUBLIC_HOST` séparée de `baseURL` Hugo

Le ping moteurs **ne doit jamais utiliser `baseURL` Hugo directement** (qui vaut `hugo-test.arleo.eu` aujourd'hui). Au lieu de ça, le MCP lit une variable `SEO_PUBLIC_HOST` du `.env` :

```bash
# .env du MCP Hugo
SEO_PUBLIC_HOST=                    # VIDE par défaut, à remplir avec arleo.eu UNIQUEMENT en prod
SEO_PINGS_ENABLED=false             # kill-switch global, false par défaut
INDEXNOW_KEY=                       # vide par défaut
GOOGLE_INDEXING_SERVICE_ACCOUNT_JSON=  # vide par défaut
```

**Comportement attendu** :

```python
import os

def is_seo_pings_safe() -> tuple[bool, str]:
    """Returns (ok, reason) — only True when ALL safety checks pass."""

    # Check 1 : kill-switch global
    if os.getenv("SEO_PINGS_ENABLED", "false").lower() != "true":
        return False, "SEO_PINGS_ENABLED is not 'true' (master kill-switch)"

    # Check 2 : SEO_PUBLIC_HOST doit être défini ET non-test
    seo_host = os.getenv("SEO_PUBLIC_HOST", "").strip()
    if not seo_host:
        return False, "SEO_PUBLIC_HOST is not set"

    # Check 3 : refuser explicitement les hosts contenant "test", "dev", "staging", "preview"
    forbidden_keywords = ["test", "dev", "staging", "preview", "localhost", "local"]
    for kw in forbidden_keywords:
        if kw in seo_host.lower():
            return False, f"SEO_PUBLIC_HOST contains forbidden keyword '{kw}': {seo_host}"

    # Check 4 : SEO_PUBLIC_HOST ne doit PAS contenir le hostname Hugo actuel si celui-ci ressemble à un environnement de test
    # (exemple : si baseURL = hugo-test.arleo.eu, on refuse même si SEO_PUBLIC_HOST = arleo.eu, car c'est suspect)
    # Cela demande une vérification croisée — voir Protection 2

    return True, "ok"
```

#### Protection 2 — Garde-fou « hostname Hugo vs hostname SEO »

Si `baseURL` du Hugo (lu via `hugo config` ou via `hugo.toml`) contient un keyword interdit, **abort SEO ping**, peu importe les autres variables :

```python
def get_hugo_baseurl() -> str:
    """Lit la baseURL depuis hugo.toml — source unique de vérité."""
    import tomllib
    with open(f"{HUGO_SITE}/hugo.toml", "rb") as f:
        cfg = tomllib.load(f)
    return cfg.get("baseURL", "")

def is_environment_safe_for_seo() -> tuple[bool, str]:
    """Vérifie que le BUILD Hugo lui-même n'est pas un test."""
    base_url = get_hugo_baseurl().lower()
    forbidden_keywords = ["test", "dev", "staging", "preview", "localhost", "local"]
    for kw in forbidden_keywords:
        if kw in base_url:
            return False, f"hugo baseURL contains '{kw}': {base_url} — refusing SEO pings"
    return True, "ok"
```

**Logique combinée à appeler avant tout ping** :

```python
async def safe_ping_seo(canonical_path: str) -> dict:
    """canonical_path : le path RELATIF, ex '/csp-nonce/'."""
    ok1, reason1 = is_seo_pings_safe()
    if not ok1:
        return {"skipped": True, "reason": reason1}

    ok2, reason2 = is_environment_safe_for_seo()
    if not ok2:
        return {"skipped": True, "reason": reason2}

    # On a passé les 2 checks → on construit l'URL avec SEO_PUBLIC_HOST,
    # JAMAIS avec la baseURL Hugo
    seo_host = os.getenv("SEO_PUBLIC_HOST").strip()
    canonical_url = f"https://{seo_host}{canonical_path}"

    results = {}
    results["indexnow"]     = await ping_indexnow([canonical_url])
    results["google_index"] = await ping_google_indexing([canonical_url])
    return results
```

#### Protection 3 — robots.txt strict sur hugo-test.arleo.eu

Indépendamment du MCP, le site de test doit avoir un `robots.txt` qui refuse tout indexage :

```
# /home/jm/hugo-site/static/robots.txt
User-agent: *
Disallow: /
```

Hugo le sert statiquement. Vérification : `curl https://hugo-test.arleo.eu/robots.txt` doit retourner ces 2 lignes.

**Bonus** : ajouter au layout de base LoveIt (ou via `hugo.toml`) un meta noindex sur l'ensemble du site quand `baseURL` contient "test" :

```toml
# Dans hugo.toml params
[params]
  ...
  # Désactiver l'indexation du site de pré-prod
  enableRobotsTXT = true
```

Et dans `layouts/partials/head.html` du thème ou en override site :

```go-html-template
{{ if or (in (lower .Site.BaseURL) "test") (in (lower .Site.BaseURL) "staging") }}
<meta name="robots" content="noindex, nofollow, noarchive">
{{ end }}
```

→ Triple ceinture : si quelqu'un (toi ou un crawler) tape `hugo-test.arleo.eu/csp-nonce/`, il y a un meta noindex dans la page elle-même, en plus du robots.txt. Et même si quelque chose passe à travers, le MCP refusera de pinger les moteurs.

### IndexNow (Bing, Yandex, Naver, Seznam)

**Endpoint** : `https://api.indexnow.org/indexnow`

**Pré-requis** : un fichier `https://{SEO_PUBLIC_HOST}/{key}.txt` contenant la même `key`. Le fichier doit être servi depuis `static/` Hugo.

**À configurer dans `.env`** (en plus des variables de la Protection 1) :

```
INDEXNOW_KEY=<32-char-hex-key>
# Note: pas de INDEXNOW_HOST séparé — on utilise SEO_PUBLIC_HOST défini plus haut
```

**Au premier run avec `SEO_PINGS_ENABLED=true`, le MCP doit** :
1. Vérifier que `static/{INDEXNOW_KEY}.txt` existe sur disque
2. Sinon, le créer automatiquement (contenu = la clé elle-même)
3. Logger : "IndexNow key file created at static/{key}.txt"
4. **NE PAS créer le fichier si SEO_PINGS_ENABLED=false** (pas la peine)

**Code à ajouter** (`indexnow.py`) :

```python
import httpx, os

async def ping_indexnow(urls: list[str]) -> dict:
    """NE JAMAIS appeler directement — utiliser safe_ping_seo() qui empile les checks."""
    key = os.getenv("INDEXNOW_KEY")
    host = os.getenv("SEO_PUBLIC_HOST")  # PAS INDEXNOW_HOST — source unique
    if not key or not host:
        return {"skipped": "INDEXNOW_KEY or SEO_PUBLIC_HOST not set"}

    payload = {
        "host": host,
        "key": key,
        "keyLocation": f"https://{host}/{key}.txt",
        "urlList": urls,
    }
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post("https://api.indexnow.org/indexnow", json=payload)
    return {"status": r.status_code, "ok": r.status_code in (200, 202)}
```

### Google Indexing API

**Plus complexe** car nécessite un service account Google + auth OAuth2.

**Pré-requis Jm devra fournir** :
1. Service account JSON (path dans `.env` : `GOOGLE_INDEXING_SERVICE_ACCOUNT_JSON=/etc/hugo-mcp/google-sa.json`)
2. Vérification de propriété du domaine dans Google Search Console
3. Service account ajouté comme "Owner" dans Search Console

**Si une de ces étapes manque → log warning et skip**, ne pas bloquer la création de page.

**Code à ajouter** (`google_indexing.py`) :

```python
import os, json
from google.oauth2 import service_account
from google.auth.transport.requests import AuthorizedSession

GOOGLE_API_URL = "https://indexing.googleapis.com/v3/urlNotifications:publish"
SCOPES = ["https://www.googleapis.com/auth/indexing"]

async def ping_google_indexing(urls: list[str], action: str = "URL_UPDATED") -> dict:
    sa_path = os.getenv("GOOGLE_INDEXING_SERVICE_ACCOUNT_JSON")
    if not sa_path or not os.path.exists(sa_path):
        return {"skipped": "no service account configured"}

    creds = service_account.Credentials.from_service_account_file(sa_path, scopes=SCOPES)
    session = AuthorizedSession(creds)

    results = []
    for url in urls:
        resp = session.post(GOOGLE_API_URL, json={"url": url, "type": action})
        results.append({"url": url, "status": resp.status_code, "ok": resp.ok})
    return {"results": results}
```

(Note : Google's docs disent que `URL_UPDATED` n'est documentés/supportés que pour des **JobPostings et BroadcastEvents**. Pour des pages classiques de blog, l'API ne garantit rien — le ping passe, mais Google peut ignorer. Mentionner ça dans la doc du tool : "best effort, no guarantee.")

### Intégration dans create_page / update_page

```python
async def tool_create_page(args):
    ping_seo = args.get("ping_seo", False)
    # ... création de la page comme avant ...

    seo_results = None
    if ping_seo:
        # Construction du PATH (pas l'URL complète) — safe_ping_seo
        # ajoutera le hostname depuis SEO_PUBLIC_HOST après vérification
        if lang and lang != "fr":
            canonical_path = f"/{lang}/{route}/"
        else:
            canonical_path = f"/{route}/"

        # IMPORTANT : safe_ping_seo empile les 3 protections
        # (kill-switch, SEO_PUBLIC_HOST valide, baseURL Hugo non-test)
        seo_results = await safe_ping_seo(canonical_path)

    return {
        "status": "created",
        "file": filepath,
        "deploy": deploy_output,
        "cf_purge": cf_result,
        "seo_pings": seo_results,
    }
```

**Pourquoi cette construction** :
- Le caller MCP envoie `ping_seo=True` mais NE FOURNIT PAS l'URL canonique complète
- Le MCP construit le **path** uniquement
- `safe_ping_seo` valide l'environnement, puis assemble `https://{SEO_PUBLIC_HOST}{path}`
- → Impossible de pinger une URL sur un host non validé, même si le caller essaye

**Tests** :

```python
# === SCÉNARIOS QUI DOIVENT BLOQUER LES PINGS ===

# 1. Test : kill-switch global OFF (état par défaut, normal pendant migration)
# Préconditions: SEO_PINGS_ENABLED=false (default)
create_page(route="/test-blocked-1", ..., ping_seo=True)
# → seo_pings.skipped=True, reason contient "SEO_PINGS_ENABLED"
# → AUCUN appel HTTPS sortant (vérifier dans logs)

# 2. Test : SEO_PUBLIC_HOST contient "test"
# Préconditions: SEO_PINGS_ENABLED=true, SEO_PUBLIC_HOST=hugo-test.arleo.eu
create_page(route="/test-blocked-2", ..., ping_seo=True)
# → seo_pings.skipped=True, reason contient "forbidden keyword 'test'"

# 3. Test : SEO_PUBLIC_HOST=arleo.eu (prod) MAIS hugo.toml baseURL=hugo-test.arleo.eu
# Préconditions: SEO_PINGS_ENABLED=true, SEO_PUBLIC_HOST=arleo.eu,
#                hugo.toml baseURL=https://hugo-test.arleo.eu
create_page(route="/test-blocked-3", ..., ping_seo=True)
# → seo_pings.skipped=True, reason contient "hugo baseURL contains 'test'"
# → C'est le filet de sécurité quand quelqu'un edit .env avant de switcher hugo.toml

# 4. Test : SEO_PUBLIC_HOST=staging.arleo.eu
create_page(route="/test-blocked-4", ..., ping_seo=True)
# → seo_pings.skipped=True, reason contient "forbidden keyword 'staging'"

# === SCÉNARIO DE PROD (toutes les protections passent) ===

# 5. Test : tout configuré pour de la vraie prod
# Préconditions: SEO_PINGS_ENABLED=true, SEO_PUBLIC_HOST=arleo.eu,
#                hugo.toml baseURL=https://arleo.eu, INDEXNOW_KEY défini,
#                Google SA configuré
create_page(route="/test-real-prod", ..., ping_seo=True)
# → seo_pings.indexnow.ok=True
# → seo_pings.google_index.results existe

# === COMPORTEMENT NORMAL ===

# 6. Test : ping_seo=False (défaut) — la couche SEO ne tourne même pas
create_page(route="/test-noping", ...)
# → seo_pings=None
# → Aucun appel à safe_ping_seo
```

**Validation manuelle finale (à faire 1 fois)** : 
1. Lancer un `tcpdump -i any -n host api.indexnow.org or host indexing.googleapis.com &` pendant les tests 1-4
2. Aucun paquet ne doit sortir vers ces destinations
3. Si paquet observé → la protection a échoué, NE PAS DÉPLOYER en prod

**Commit** :
- Message : `feat: IndexNow and Google Indexing API ping on create/update`
- Bump version : v1.4.0 → v1.5.0
- Documentation : section "SEO" dans le README expliquant comment configurer les clés

**Checklist `.env` pour le jour du switch test → prod** (à intégrer au README) :

Quand Jm bascule la VM `hugo-test.arleo.eu` vers `arleo.eu` (prod) :

```bash
# AVANT le switch — état par défaut, sécurisé
SEO_PINGS_ENABLED=false
SEO_PUBLIC_HOST=
INDEXNOW_KEY=
GOOGLE_INDEXING_SERVICE_ACCOUNT_JSON=

# APRÈS le switch hugo.toml baseURL → arleo.eu — activer dans cet ordre :
# 1. Modifier hugo.toml: baseURL = "https://arleo.eu"  (rebuild, vérifier site OK)
# 2. Modifier .env: SEO_PUBLIC_HOST=arleo.eu
# 3. Modifier .env: INDEXNOW_KEY=<la clé prod>
# 4. Modifier .env: GOOGLE_INDEXING_SERVICE_ACCOUNT_JSON=/etc/hugo-mcp/google-sa.json
# 5. SEULEMENT EN DERNIER: SEO_PINGS_ENABLED=true
# 6. Restart hugo-mcp service
# 7. Test : create_page sur une page sample avec ping_seo=True, vérifier seo_pings.indexnow.ok=True
# 8. Vérifier dans Bing Webmaster Tools que le ping est bien arrivé
```

Cette séquence garantit qu'à aucun moment le site de test ne ping les moteurs.

**STOP en fin de Phase 4**, validation.

---

## Phase 5 — Sitemap multilingue avec hreflang

**Problème** : Hugo génère un `sitemap.xml` natif, mais **sans annotations hreflang multilingues**. Pour une bonne SEO multilingue, chaque URL doit déclarer ses traductions :

```xml
<url>
  <loc>https://arleo.eu/csp-nonce/</loc>
  <xhtml:link rel="alternate" hreflang="fr" href="https://arleo.eu/csp-nonce/" />
  <xhtml:link rel="alternate" hreflang="en" href="https://arleo.eu/en/csp-nonce/" />
  <xhtml:link rel="alternate" hreflang="x-default" href="https://arleo.eu/csp-nonce/" />
  <lastmod>2026-04-15T11:19:00+02:00</lastmod>
</url>
```

**Solution** : override le template Hugo `sitemap.xml` côté thème ou layouts/. Ce n'est PAS une modification du MCP, c'est une **modif du site Hugo lui-même** (`/home/jm/hugo-site/layouts/_default/sitemap.xml`).

**Template attendu** (à créer dans le repo Hugo, pas dans le MCP) :

```go-html-template
<?xml version="1.0" encoding="utf-8" standalone="yes"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"
        xmlns:xhtml="http://www.w3.org/1999/xhtml">
  {{ range .Data.Pages }}
  <url>
    <loc>{{ .Permalink }}</loc>
    {{ range .AllTranslations }}
    <xhtml:link rel="alternate" hreflang="{{ .Lang }}" href="{{ .Permalink }}" />
    {{ end }}
    {{ if .IsTranslated }}
    <xhtml:link rel="alternate" hreflang="x-default" href="{{ (index .AllTranslations 0).Permalink }}" />
    {{ end }}
    {{ if not .Lastmod.IsZero }}
    <lastmod>{{ .Lastmod.Format "2006-01-02T15:04:05-07:00" }}</lastmod>
    {{ end }}
    {{ with .Sitemap.ChangeFreq }}<changefreq>{{ . }}</changefreq>{{ end }}
    {{ if ge .Sitemap.Priority 0.0 }}<priority>{{ .Sitemap.Priority }}</priority>{{ end }}
  </url>
  {{ end }}
</urlset>
```

**Validation** :

```bash
# Après build, le sitemap doit contenir les balises hreflang
curl https://hugo-test.arleo.eu/sitemap.xml | grep -A 5 "csp-nonce"
# → Doit afficher :
# <loc>https://hugo-test.arleo.eu/csp-nonce/</loc>
# <xhtml:link rel="alternate" hreflang="fr" href="..." />
# <xhtml:link rel="alternate" hreflang="en" href="..." />

# Validation XML
xmllint --noout https://hugo-test.arleo.eu/sitemap.xml && echo OK
```

**Bonus à intégrer** : ajouter une option `sitemap.priority` dans la spec frontmatter (`sitemap: { priority: 0.8, changefreq: "monthly" }`) pour que les pages importantes priment.

**Commit dans le repo `/home/jm/hugo-site`** (pas le MCP) :
- Message : `feat: multilingual sitemap with hreflang annotations`

---

## Phase 6 — Récap & docs

À la toute fin :

1. Fichier `MIGRATION-NOTES-2026-05-07.md` dans chaque repo MCP listant les changements pour Jm
2. README de chaque MCP mis à jour avec les nouveaux paramètres
3. Tag `v2.0.0` sur le MCP Hugo (breaking changes accumulés depuis v1.3.0)
4. Tag équivalent sur le MCP Grav après le bug fix

---

## Ordre d'exécution recommandé

```
Phase 1  →  validation  →  STOP
Phase 2  →  rapport     →  validation Jm sur findings
Phase 2bis (optionnel)  →  patches sécu validés
Phase 3  →  validation
Phase 4  →  validation (Jm doit fournir les clés IndexNow + Google SA)
Phase 5  →  validation (modif Hugo, pas MCP)
Phase 6  →  récap final
```

**Si Jm dit "stop" à n'importe quelle phase, STOP. Ne pas tenter de continuer "tant qu'on y est".**
