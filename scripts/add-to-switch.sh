#!/usr/bin/env bash
# Ajoute un dépôt ticoshop au Store de tico, directement sur la carte SD de la Switch.
#
#   curl -fsSL https://shop.example.com/add-to-switch.sh | bash -s -- /chemin/vers/SD
#   ./add-to-switch.sh /chemin/vers/SD [URL_DE_INDEX_JSON]
#
# tico 0.8 garde ses dépôts dans sdmc:/tico/config/store_repos.json
# ({"repos": [...], "trusted": [...], "seen_suggested": [...]}).
# Le fichier existant est sauvegardé en .bak avant toute modification.
# En cas de doute, la méthode sûre reste : tico › Store › Sources › Ajouter un dépôt.
set -euo pipefail

INDEX_URL="${2:-__TICOSHOP_INDEX_URL__}"
SD="${1:-}"

die() { echo "❌ $*" >&2; exit 1; }

if [[ -z "$SD" ]]; then
  echo "Usage : $0 /chemin/vers/la/carte/SD [url-index.json]"
  echo "Exemples : /media/\$USER/SWITCH (Linux), /Volumes/SWITCH (macOS), /e (Git Bash)"
  exit 1
fi
[[ "$INDEX_URL" == http://* || "$INDEX_URL" == https://* ]] || die "URL de dépôt invalide : $INDEX_URL"
[[ -d "$SD" ]] || die "Dossier introuvable : $SD"
SD="${SD%/}"

if [[ ! -d "$SD/tico" ]]; then
  echo "⚠️  Pas de dossier tico/ sur $SD : tico n'a pas encore été lancé sur cette carte ?"
  read -r -p "Continuer quand même ? [o/N] " ok </dev/tty || ok=""
  [[ "$ok" =~ ^[oOyY]$ ]] || exit 1
fi

# Un fichier par compte si tico en crée plusieurs ; sinon le fichier global.
# (boucle while plutôt que mapfile : le bash 3.2 de macOS ne l'a pas)
FILES=()
while IFS= read -r f; do FILES+=("$f"); done < <(find "$SD/tico" -name store_repos.json -type f 2>/dev/null || true)
if [[ ${#FILES[@]} -eq 0 ]]; then
  FILES=("$SD/tico/config/store_repos.json")
fi

add_with_python() {
  "$1" - "$2" "$INDEX_URL" <<'PY'
import json, sys, os
path, url = sys.argv[1], sys.argv[2]
data = {}
if os.path.exists(path) and os.path.getsize(path) > 0:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
if not isinstance(data, dict):
    sys.exit("format inattendu (objet JSON attendu)")
repos = data.setdefault("repos", [])
data.setdefault("trusted", [])
data.setdefault("seen_suggested", [])
def url_of(r):
    return r if isinstance(r, str) else (r or {}).get("url")
if any(url_of(r) == url for r in repos):
    print("already")
    sys.exit(0)
# Même forme que les entrées existantes (chaîne ou objet {"url": ...}).
repos.append({"url": url} if repos and isinstance(repos[0], dict) else url)
tmp = path + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2, ensure_ascii=False)
os.replace(tmp, path)
print("added")
PY
}

add_with_jq() {
  local path="$1" tmp="$1.tmp"
  [[ -s "$path" ]] || echo '{}' >"$path"
  if jq -e --arg u "$INDEX_URL" '(.repos // []) | map(if type == "string" then . else .url end) | index($u)' "$path" >/dev/null; then
    echo already; return
  fi
  jq --arg u "$INDEX_URL" '
    .repos = ((.repos // []) + [ if ((.repos // [])[0] | type) == "object" then {url: $u} else $u end ])
    | .trusted = (.trusted // []) | .seen_suggested = (.seen_suggested // [])' "$path" >"$tmp"
  mv "$tmp" "$path"
  echo added
}

PY=""
for c in python3 python; do command -v "$c" >/dev/null 2>&1 && { PY="$c"; break; }; done

for f in "${FILES[@]}"; do
  mkdir -p "$(dirname "$f")"
  [[ -f "$f" ]] && cp "$f" "$f.bak"
  if [[ -n "$PY" ]]; then
    res=$(add_with_python "$PY" "$f")
  elif command -v jq >/dev/null 2>&1; then
    res=$(add_with_jq "$f")
  elif [[ ! -s "$f" ]]; then
    printf '{\n  "repos": [\n    "%s"\n  ],\n  "trusted": [],\n  "seen_suggested": []\n}\n' "$INDEX_URL" >"$f"
    res=added
  else
    die "python3 ou jq est nécessaire pour modifier $f. Ajoutez le dépôt depuis tico : Store › Sources › Ajouter un dépôt › $INDEX_URL"
  fi
  case "$res" in
    already) echo "✔ Déjà présent dans ${f#"$SD"/}" ;;
    added)   echo "✔ Dépôt ajouté dans ${f#"$SD"/}" ;;
    *)       die "Échec sur $f : $res" ;;
  esac
done

sync 2>/dev/null || true
echo
echo "Terminé. Éjectez proprement la carte SD, lancez tico puis ouvrez le Store."
echo "Si le dépôt n'apparaît pas, ajoutez-le à la main : Store › Sources › Ajouter un dépôt"
echo "  $INDEX_URL"
