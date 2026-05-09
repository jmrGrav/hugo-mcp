# Backlog MCP — Tool upload_asset

**Statut** : BACKLOG — feature future
**Priorité** : Medium (utilité claire mais pas bloquant)
**Estimation** : ~3h
**Date création** : 2026-05-09

## Contexte

Le Hugo MCP Server v1.8.1 expose 7 tools : `list_pages`, `get_page`, `create_page`, `update_page`, `delete_page`, `build_site`, `list_assets`. Aucun ne permet à un client (Claude.ai) d'uploader un fichier binaire (image, PDF, etc.).

Conséquence opérationnelle : pour ajouter une featured image à un article, il faut SSH dans la VM Hugo et copier le fichier manuellement. Ça casse le workflow autonome où Claude pourrait :

1. Générer une image via DALL-E / Imagen / autre API
2. Uploader directement dans le page bundle
3. Mettre à jour le frontmatter pour pointer dessus
4. Tout en restant dans la conversation Claude.ai

## Spécification proposée

### Nouveau tool : `upload_asset`

```python
@mcp.tool()
def upload_asset(
    path: str,           # ex: "posts/mon-article/featured.png"
    content_b64: str,    # contenu binaire encodé base64
    mime_type: str,      # ex: "image/png", "image/svg+xml"
    overwrite: bool = False
) -> dict:
    """
    Upload an asset file to either static/ or a content page bundle.

    Path conventions:
      - "static/<filename>" → /home/jm/hugo-site/static/<filename>
      - "posts/<route>/<filename>" → /home/jm/hugo-site/content/posts/<route>/<filename>

    Returns: {"status": "uploaded", "path": "...", "size_bytes": N}
    """
```

### Sécurité

Réutilise des primitives déjà en place dans v1.6.0 / v1.7.0 / v1.8.1 :

1. **Path validation** (réutilise H-01 fix v1.3.1) :
   - Pas de `..`, pas de leading `/`
   - Whitelist préfixes : `static/`, `posts/<existing-route>/`
   - Reject si la route n'existe pas (no orphan bundle creation)

2. **Size limit** (réutilise H-05 body limit v1.4.0) :
   - Max 5 MB par fichier
   - Rejeter `content_b64` qui décodé dépasse 5 MB

3. **MIME validation** (nouveau) :
   - Whitelist : `image/png`, `image/jpeg`, `image/svg+xml`, `image/webp`, `image/gif`, `application/pdf`
   - Vérifier le magic number (file header) en plus du `mime_type` déclaré
   - Utiliser `python-magic` ou `imghdr`
   - Reject si mismatch déclaré vs réel

4. **Atomic write** :
   - Écrire dans `/tmp/upload_<uuid>.tmp` puis `os.rename()` pour atomicité
   - Évite les fichiers corrompus si interruption

5. **Audit log** (réutilise H-06 v1.6.0) :
   - Log JSON structuré : `{event, path, size_bytes, mime_type, source_ip, ts}`
   - Ingéré dans BetterStack via Vector

### Tests à ajouter

- OK : Upload PNG 100KB dans `static/` → succès
- OK : Upload SVG 50KB dans `posts/<existing-route>/` → succès
- KO : Upload 10MB PNG → reject (size limit)
- KO : Upload .exe maquillé en .png (mismatch magic) → reject
- KO : Upload dans `posts/<non-existing-route>/` → reject (no orphan)
- KO : Path traversal (`../etc/passwd`) → reject (réutilise H-01)
- KO : MIME non whitelisté (`text/html`) → reject
- OK : `overwrite=true` sur fichier existant → succès
- KO : `overwrite=false` sur fichier existant → conflict 409

### Workflow Claude.ai type

```python
import base64

img_bytes = generate_image_via_api(prompt="...")
img_b64 = base64.b64encode(img_bytes).decode('ascii')

upload_asset(path="posts/mon-article/featured.png", content_b64=img_b64, mime_type="image/png")
update_page(route="/posts/mon-article", lang="fr", frontmatter={"featuredImagePreview": "featured.png"})
```

3 appels MCP, autonome, pas de SSH.

## Implémentation estimée

- ~2h coding + tests unitaires
- ~30min ajout aux tests reproductibles
- ~15min CHANGELOG + bump version
- ~15min documentation README

Total : ~3h. ETA : juin 2026.

## Quand l'implémenter

Après le sprint sécurité MCP. Plusieurs primitives nécessaires (size limit révisée, audit log JSON, validation MIME stricte) sont des chantiers ou prérequis du sprint en cours.
