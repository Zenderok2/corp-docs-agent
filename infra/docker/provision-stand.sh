#!/usr/bin/env bash
set -euo pipefail

if [[ "$#" -ne 3 ]] || [[ "$(id -u)" -ne 0 ]] || [[ ! "$1" =~ ^[a-f0-9]{40}$ ]]; then
  printf 'Usage: sudo bash provision-stand.sh RELEASE_SHA STOP_AT MOSCOW_PUBLIC_KEY_PEM_BASE64\n' >&2
  exit 1
fi
release=$1
stop_at=$2
recovery_public_key=$3
release_directory=/opt/corp-docs-agent

install -d -m 0755 "$release_directory"
cd "$release_directory"
if [[ -e .env ]]; then
  printf 'An existing stand is already configured at %s\n' "$release_directory" >&2
  exit 1
fi
curl --fail --silent --show-error --location --retry 3 --max-time 90 \
  "https://codeload.github.com/Zenderok2/corp-docs-agent/tar.gz/$release" \
  | tar --extract --gzip --strip-components=1

bash infra/docker/bootstrap-yandex-vm.sh "$stop_at"

address=$(curl --fail --silent --show-error --retry 3 --connect-timeout 3 --max-time 10 \
  -H 'Metadata-Flavor: Google' \
  'http://169.254.169.254/computeMetadata/v1/instance/network-interfaces/0/access-configs/0/external-ip')
if [[ ! "$address" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]; then
  printf 'Expected a public IPv4 on the VM\n' >&2
  exit 1
fi
site="${address//./-}.sslip.io"

key_file=$(mktemp)
trap 'rm -f -- "$key_file"' EXIT
printf '%s' "$recovery_public_key" | base64 --decode > "$key_file"
openssl pkey -pubin -in "$key_file" -noout

quality_code=$(openssl rand -hex 16)
session_secret=$(openssl rand -hex 32)
(
  umask 077
  cat > .env <<EOF
APP_ENV=hosted
PUBLIC_ORIGIN=https://$site
SITE_ADDRESS=$site
HTTP_BIND=0.0.0.0:80
HTTPS_BIND=0.0.0.0:443
BUILD_SHA=$release
RETRIEVAL_MODE=lexical
QUALITY_ACCESS_CODE=$quality_code
SESSION_SECRET=$session_secret
EOF
)
printf 'CORP_DOCS_QUALITY_CODE_ENCRYPTED='
printf '%s' "$quality_code" \
  | openssl pkeyutl -encrypt -pubin -inkey "$key_file" -pkeyopt rsa_padding_mode:oaep -pkeyopt rsa_oaep_md:sha256 \
  | base64 -w0
printf '\n'
unset quality_code session_secret

mkdir -p runtime/public
bash infra/docker/fetch-models.sh
docker compose config --quiet
docker compose build api web
docker compose run --rm --no-deps operator
docker compose up -d

for _ in {1..36}; do
  if curl --fail --silent --max-time 7 --resolve "$site:443:127.0.0.1" "https://$site/health/ready"; then
    printf '\nCORP_DOCS_STAND_READY=https://%s\n' "$site"
    exit 0
  fi
  sleep 5
done
docker compose ps
printf 'Stand readiness verification timed out for https://%s\n' "$site" >&2
exit 1
