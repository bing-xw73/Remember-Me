#!/bin/sh
# SPDX-License-Identifier: CPAL-1.0
set -eu
cd /opt/remember-me
domain=rm.wu-bing.xyz
site=/etc/nginx/sites-available/remember-me
if [ ! -f "/etc/letsencrypt/live/$domain/fullchain.pem" ]; then
    cat >"$site" <<'NGINX'
# Remember-Me ACME bootstrap
server {
    listen 80;
    server_name rm.wu-bing.xyz;
    access_log off;
    error_log /dev/null;
    location ^~ /.well-known/acme-challenge/ { root /var/www/remember-me-acme; }
    location / { return 404; }
}
NGINX
    ln -sfn "$site" /etc/nginx/sites-enabled/remember-me
    nginx -t
    systemctl reload nginx
    certbot certonly --webroot -w /var/www/remember-me-acme -d "$domain" --non-interactive --agree-tos --register-unsafely-without-email
fi
sed "s/DOMAIN/$domain/g" deploy/nginx-site.conf.template >"$site"
chmod 644 "$site"
ln -sfn "$site" /etc/nginx/sites-enabled/remember-me
nginx -t
systemctl reload nginx
install -d -m 755 /etc/letsencrypt/renewal-hooks/deploy
printf '#!/bin/sh\nsystemctl reload nginx\n' >/etc/letsencrypt/renewal-hooks/deploy/remember-me-reload-nginx
chmod 755 /etc/letsencrypt/renewal-hooks/deploy/remember-me-reload-nginx
echo 'Remember-Me HTTPS configured.'
