#!/usr/bin/env bash
# Installe et démarre ticoshop avec Docker.
#
#   ./scripts/install.sh                         # interactif
#   ./scripts/install.sh --url https://shop.example.com --port 8080
#
# Crée .env (mot de passe admin, jeton d'API et clé de session aléatoires),
# construit l'image et lance le conteneur. Relançable sans risque : un .env
# existant est conservé, seule l'image est reconstruite (= mise à jour).
set -euo pipefail

cd "$(dirname "$0")/.."

URL="" PORT=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --url)  URL="$2"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
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

if [[ ! -f .env ]]; then
  PORT="${PORT:-8080}"
  if [[ -z "$URL" ]]; then
    default="http://$(guess_ip):$PORT"
    if [[ -t 0 ]]; then
      read -r -p "Adresse publique du shop (vue par la Switch) [$default] : " URL
    fi
    URL="${URL:-$default}"
  fi
  ADMIN_PASSWORD="$(rand 8)"
  sed -e "s|^TICOSHOP_PUBLIC_URL=.*|TICOSHOP_PUBLIC_URL=${URL%/}|" \
      -e "s|^TICOSHOP_PORT=.*|TICOSHOP_PORT=$PORT|" \
      -e "s|^TICOSHOP_ADMIN_PASSWORD=.*|TICOSHOP_ADMIN_PASSWORD=$ADMIN_PASSWORD|" \
      -e "s|^TICOSHOP_API_TOKEN=.*|TICOSHOP_API_TOKEN=$(rand 24)|" \
      -e "s|^TICOSHOP_SECRET_KEY=.*|TICOSHOP_SECRET_KEY=$(rand 32)|" \
      .env.example > .env
  chmod 600 .env
  echo "✔ .env créé"
else
  echo "✔ .env existant conservé"
fi

mkdir -p data
# Le conteneur tourne avec l'UID 1000.
if [[ "$(id -u)" == 0 ]]; then chown -R 1000:1000 data; fi

"${COMPOSE[@]}" up -d --build

# shellcheck disable=SC1091
set -a; source .env; set +a
echo
echo "ticoshop démarre…"
for _ in $(seq 1 30); do
  if curl -fsS "http://127.0.0.1:${TICOSHOP_PORT}/healthz" >/dev/null 2>&1; then break; fi
  sleep 1
done

cat <<EOF

==================================================================
 ticoshop est prêt
------------------------------------------------------------------
 Vitrine          : ${TICOSHOP_PUBLIC_URL}/
 Administration   : ${TICOSHOP_PUBLIC_URL}/admin
 Mot de passe     : ${TICOSHOP_ADMIN_PASSWORD}
 URL pour tico    : ${TICOSHOP_PUBLIC_URL}/index.json
 Jeton API (CLI)  : ${TICOSHOP_API_TOKEN}
------------------------------------------------------------------
 CLI :  export TICOSHOP_URL=${TICOSHOP_PUBLIC_URL} TICOSHOP_TOKEN=<jeton>
        ./scripts/ticoshop list
==================================================================
EOF
