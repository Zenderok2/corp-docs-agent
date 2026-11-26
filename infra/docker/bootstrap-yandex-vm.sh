#!/usr/bin/env bash
set -euo pipefail

if [[ "$#" -ne 1 ]] || [[ "$(id -u)" -ne 0 ]]; then
  printf 'Использование: sudo bash %s "YYYY-MM-DD HH:MM:SS Europe/Moscow"\n' "$0" >&2
  exit 1
fi
end=$1
if [[ ! "$end" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}\ [0-9]{2}:[0-9]{2}:[0-9]{2}\ Europe/Moscow$ ]]; then
  printf 'Укажите дату и московское время окончания работы.\n' >&2
  exit 1
fi
systemd-analyze calendar "$end" >/dev/null

cat > /etc/systemd/system/corp-docs-window.timer <<EOF
[Unit]
Description=Stop the contest VM after the judging window

[Timer]
OnCalendar=$end
Persistent=true
AccuracySec=1min

[Install]
WantedBy=timers.target
EOF

cat > /etc/systemd/system/corp-docs-window.service <<'EOF'
[Unit]
Description=Finish the contest VM operating window

[Service]
Type=oneshot
ExecStart=-/usr/bin/systemctl disable corp-docs-window.timer
ExecStart=/usr/sbin/poweroff
EOF

systemctl daemon-reload
systemctl enable --now corp-docs-window.timer

export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y --no-install-recommends -o DPkg::Lock::Timeout=120 docker.io docker-compose-v2 curl ca-certificates
apt-get clean
systemctl enable --now docker
docker compose version
systemctl list-timers corp-docs-window.timer --no-pager
