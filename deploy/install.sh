#!/bin/sh
# SPDX-License-Identifier: CPAL-1.0
# Run as root from /opt/remember-me; does not modify other nginx sites.
set -eu
cd /opt/remember-me
if ss -ltnH 'sport = :8787' | grep -q . && ! systemctl is-active --quiet remember-me; then
    echo 'Port 8787 is already occupied; refusing installation.' >&2
    exit 1
fi
getent passwd remember-me >/dev/null || useradd --system --home /var/lib/remember-me --shell /usr/sbin/nologin remember-me
install -d -m 700 /etc/remember-me
install -d -o remember-me -g remember-me -m 700 /var/lib/remember-me
install -d -m 755 /var/www/remember-me-acme
python3 -m venv .venv
.venv/bin/pip install -c deploy/constraints.txt '.[standalone]'
if [ ! -f /etc/remember-me/host.env ]; then
    install -m 600 deploy/host.env.example /etc/remember-me/host.env
fi
python3 - <<'PY'
from pathlib import Path
import secrets
p = Path('/etc/remember-me/nginx-mcp.conf')
if not p.exists():
    source = Path('deploy/nginx-mcp.conf.template').read_text()
    source = source.replace('PATH_KEY', secrets.token_urlsafe(48)).replace('HOST_TOKEN', secrets.token_urlsafe(48)).replace('DOMAIN', 'rm.wu-bing.xyz')
    p.write_text(source)
    p.chmod(0o600)
upload_token = Path('/etc/remember-me/upload-token')
if not upload_token.exists():
    upload_token.write_text(secrets.token_urlsafe(48))
    upload_token.chmod(0o600)
PY
install -m 644 deploy/remember-me.service deploy/remember-me-backup.service deploy/remember-me-backup.timer /etc/systemd/system/
chmod 755 deploy/backup.sh
systemctl daemon-reload
systemctl enable remember-me remember-me-backup.timer
systemctl restart remember-me
systemctl start remember-me-backup.timer
echo 'Host installed on loopback; configure ACME HTTP site and TLS before connecting clients.'
