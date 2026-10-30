#!/bin/sh
# SPDX-License-Identifier: CPAL-1.0
set -eu
backup_dir=/var/backups/remember-me
umask 077
install -d -m 700 "$backup_dir"
# Stop the single writer before archiving DB + CAS + host extensions together.
was_active=0
if systemctl is-active --quiet remember-me; then was_active=1; fi
restart_host() { if [ "$was_active" = 1 ]; then systemctl start remember-me; fi; }
trap restart_host EXIT
systemctl stop remember-me
archive="$backup_dir/remember-me-$(date -u +%Y%m%dT%H%M%SZ).tar.gz"
tar --exclude=.writer.lock -czf "$archive" -C /var/lib remember-me
tar -tzf "$archive" >/dev/null
# Retention: 14 days. An off-host encrypted copy must be configured separately.
find "$backup_dir" -maxdepth 1 -type f -name 'remember-me-*.tar.gz' -mtime +14 -delete
