#!/usr/bin/env bash
# Installe (ou met à jour) ticoshop avec Docker.
#
#   ./scripts/install.sh                                  # interactif, réseau local
#   ./scripts/install.sh --url http://192.168.1.20:8080   # adresse imposée
#   ./scripts/install.sh --domain shop.mondomaine.fr      # HTTPS automatique (Caddy + Let's Encrypt)
#   ./scripts/install.sh --image                          # image publiée par la CI au lieu d'un build local
#   ./scripts/install.sh --port 9000
#
# Crée .env (mot de passe admin, jeton d'API et clé de session aléatoires),
# puis lance le(s) conteneur(s). Relançable sans risque : un .env existant est
# conservé (seules les options passées le modifient) et l'image est mise à jour.
set -euo pipefail

cd "$(dirname "$0")/.."

URL="" PORT="" DOMAIN="" USE_IMAGE=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --url)    URL="$2"; shift 2 ;;
    --port)   PORT="$2"; shift 2 ;;
    --domain) DOMAIN="$2"; shift 2 ;;
    --image)  USE_IMAGE=1; shift ;;
    -h|--help) sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Option inconnue : $1" >&2; exit 1 ;;
  esac
done

command -v docker >/dev/null || { echo "❌ Docker n'est pas installé : https://docs.docker.com/engine/install/" >&2; exit 1; }
if docker compose version >/dev/null 2>&1; then
  COMPOSE=(docker compose)
elif command -v docker-compose >/dev/null; then
  COMPOSE=(docker-compose)
else
  echo "❌ Docker Compose est introuvable." >&2; exit 1
fi

# $1 octets aléatoires en hexadécimal
rand() { od -An -tx1 -N "$1" /dev/urandom | tr -d ' \n'; }

guess_ip() {
  (hostname -I 2>/dev/null || ipconfig getifaddr en0 2>/dev/null || echo 127.0.0.1) | awk '{print $1}'
}

# Remplace (ou ajoute) VAR=valeur dans .env, même si la ligne est commentée.
set_env() {
  local key="$1" value="$2" tmp
  tmp=$(mktemp)
  if grep -qE "^#?${key}=" .env; then
    awk -v k="$key" -v v="$value" '$0 ~ "^#?" k "=" { print k "=" v; next } { print }' .env >"$tmp"
  else
    cat .env >"$tmp"; echo "${key}=${value}" >>"$tmp"
  fi
  cat "$tmp" >.env; rm -f "$tmp"
}

if [[ ! -f .env ]]; then
  cp .env.example .env
  chmod 600 .env
  set_env TICOSHOP_ADMIN_PASSWORD "$(rand 8)"
  set_env TICOSHOP_API_TOKEN "$(rand 24)"
  set_env TICOSHOP_SECRET_KEY "$(rand 32)"
  if [[ -z "$URL" && -z "$DOMAIN" ]]; then
    default="http://$(guess_ip):${PORT:-8080}"
    if [[ -t 0 ]]; then
      read -r -p "Adresse du shop vue par la Switch [$default] (ou un nom de domaine pour HTTPS) : " URL
    fi
    URL="${URL:-$default}"
    # Un nom de domaine nu (sans http) active le HTTPS.
    if [[ "$URL" != http://* && "$URL" != https://* ]]; then DOMAIN="$URL"; URL=""; fi
  fi
  echo "✔ .env créé"
else
  echo "✔ .env existant conservé"
fi

[[ -n "$PORT" ]] && set_env TICOSHOP_PORT "$PORT"
[[ -n "$URL" ]] && set_env TICOSHOP_PUBLIC_URL "${URL%/}"
if [[ -n "$DOMAIN" ]]; then
  DOMAIN="${DOMAIN#https://}"; DOMAIN="${DOMAIN%%/*}"
  set_env TICOSHOP_DOMAIN "$DOMAIN"
  set_env TICOSHOP_PUBLIC_URL "https://$DOMAIN"
  set_env COMPOSE_PROFILES https
  set_env TICOSHOP_BIND 127.0.0.1
  set_env FORWARDED_ALLOW_IPS "*"
  echo "✔ HTTPS activé pour $DOMAIN (le domaine doit pointer vers ce serveur, ports 80/443 ouverts)"
fi

mkdir -p data backups
# Le conteneur tourne avec l'UID 1000.
if [[ "$(id -u)" == 0 ]]; then chown -R 1000:1000 data backups; fi

if [[ "$USE_IMAGE" == 1 ]]; then
  "${COMPOSE[@]}" pull --ignore-buildable 2>/dev/null || "${COMPOSE[@]}" pull
  "${COMPOSE[@]}" up -d --no-build
else
  "${COMPOSE[@]}" up -d --build
fi

# shellcheck disable=SC1091
set -a; source .env; set +a
echo
echo "ticoshop démarre…"
for _ in $(seq 1 30); do
  if curl -fsS "http://127.0.0.1:${TICOSHOP_PORT:-8080}/healthz" >/dev/null 2>&1; then break; fi
  sleep 1
done

cat <<EOF

==================================================================
 ticoshop est prêt
------------------------------------------------------------------
 Vitrine          : ${TICOSHOP_PUBLIC_URL}/
 Administration   : ${TICOSHOP_PUBLIC_URL}/admin   (identifiant : admin)
 Mot de passe     : ${TICOSHOP_ADMIN_PASSWORD}
 URL pour tico    : ${TICOSHOP_PUBLIC_URL}/index.json
 Jeton API (CLI)  : ${TICOSHOP_API_TOKEN}
 Diagnostic       : ${TICOSHOP_PUBLIC_URL}/admin/diagnostic
------------------------------------------------------------------
 CLI :  export TICOSHOP_URL=${TICOSHOP_PUBLIC_URL} TICOSHOP_TOKEN=<jeton>
        ./scripts/ticoshop list
==================================================================
EOF
