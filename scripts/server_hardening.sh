#!/bin/bash
# One-shot, idempotent production hardening for the Skoolgpt VPS.
# Run as root on the server:  sudo bash scripts/server_hardening.sh
# Safe to re-run. Does NOT restart the aitutor service (deploy.sh does that).
set -euo pipefail

APP=/usr/share/nginx/aitutor.qlass.in/public_python_aios
APP_USER=qlassedTechKaushal
PGDATA=/var/lib/pgsql/18/data
NGX=/etc/nginx/sites-available

log() { printf '\n### %s\n' "$*"; }

log "1/9 Postgres: close the internet-wide pg_hba rule (docker subnets + localhost only)"
cp -n "$PGDATA/pg_hba.conf" "$PGDATA/pg_hba.conf.bak-$(date +%F)"
sed -i -E 's#^host\s+all\s+all\s+0\.0\.0\.0/0\s+md5#host    all             all             172.16.0.0/12           scram-sha-256#' "$PGDATA/pg_hba.conf"
grep -vE '^\s*#|^\s*$' "$PGDATA/pg_hba.conf"
systemctl reload postgresql-18

log "2/9 Firewall: block Postgres (5432) and pgAdmin (5050) from the internet, persisted across reboot"
cat > /usr/local/sbin/skoolgpt-firewall.sh <<'EOF'
#!/bin/bash
iptables -D INPUT -p tcp --dport 5432 -j SKOOLGPT_PG 2>/dev/null || true
iptables -F SKOOLGPT_PG 2>/dev/null || true
iptables -X SKOOLGPT_PG 2>/dev/null || true
iptables -N SKOOLGPT_PG
iptables -A SKOOLGPT_PG -s 127.0.0.0/8 -j RETURN
iptables -A SKOOLGPT_PG -s 172.16.0.0/12 -j RETURN
iptables -A SKOOLGPT_PG -j DROP
iptables -I INPUT -p tcp --dport 5432 -j SKOOLGPT_PG
ip6tables -D INPUT -p tcp --dport 5432 ! -s ::1 -j DROP 2>/dev/null || true
ip6tables -I INPUT -p tcp --dport 5432 ! -s ::1 -j DROP
if iptables -L DOCKER-USER -n >/dev/null 2>&1; then
  iptables -D DOCKER-USER -p tcp -m conntrack --ctorigdstport 5050 ! -s 127.0.0.0/8 -j DROP 2>/dev/null || true
  iptables -I DOCKER-USER -p tcp -m conntrack --ctorigdstport 5050 ! -s 127.0.0.0/8 -j DROP
fi
EOF
chmod 755 /usr/local/sbin/skoolgpt-firewall.sh
cat > /etc/systemd/system/skoolgpt-firewall.service <<'EOF'
[Unit]
Description=Skoolgpt targeted firewall rules (Postgres, pgAdmin)
After=network-online.target docker.service
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/skoolgpt-firewall.sh
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now skoolgpt-firewall.service
iptables -S INPUT | grep 5432 || true
iptables -S DOCKER-USER 2>/dev/null | grep 5050 || true

log "3/9 Secrets: .env readable by the app user only"
chmod 600 "$APP/.env"; chown "$APP_USER" "$APP/.env"; stat -c '%a %U' "$APP/.env"

log "4/9 Memory: 2 GB swap, low swappiness"
if ! swapon --show | grep -q /swapfile; then
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile >/dev/null && swapon /swapfile
fi
grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
echo 'vm.swappiness=10' > /etc/sysctl.d/90-swappiness.conf && sysctl -q -p /etc/sysctl.d/90-swappiness.conf
swapon --show

log "5/9 Logs: cap journald at 1 GB"
journalctl --vacuum-size=500M >/dev/null 2>&1 || true
grep -q '^SystemMaxUse' /etc/systemd/journald.conf || sed -i 's/^\[Journal\]/[Journal]\nSystemMaxUse=1G/' /etc/systemd/journald.conf
systemctl restart systemd-journald
du -sh /var/log/journal

log "6/9 systemd: start after Postgres/Redis, back off on crash, trust proxy headers from nginx"
cat > /etc/systemd/system/aitutor.service <<EOF
[Unit]
Description=Skoolgpt AI Tutor API
After=network-online.target postgresql-18.service redis.service
Wants=network-online.target
StartLimitIntervalSec=0

[Service]
User=$APP_USER
WorkingDirectory=$APP/backend
EnvironmentFile=$APP/.env
ExecStart=$APP/venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8096 --workers 2 --proxy-headers --forwarded-allow-ips=127.0.0.1
Restart=always
RestartSec=3
TimeoutStopSec=30
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
echo "aitutor.service updated (takes effect on next restart)"

log "7/9 nginx: upload size, timeouts, HSTS, forwarded proto; aitutor.qlass.in -> 301 skoolgpt.in"
cat > "$NGX/be-skoolgpt.skoolgpt.in.conf" <<'EOF'
server {
    server_name be-skoolgpt.skoolgpt.in;

    client_max_body_size 10m;
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;

    # WebSocket routes (e.g. the real-time voice-call feature) need the
    # Upgrade/Connection headers forwarded and a long read/send timeout —
    # a plain proxy_pass silently 404s a WS upgrade otherwise, since nginx
    # falls back to treating it as a normal HTTP request.
    location /ws/ {
        proxy_pass http://127.0.0.1:8096;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
        proxy_connect_timeout 10s;
    }

    location / {
        proxy_pass http://127.0.0.1:8096;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 120s;
        proxy_connect_timeout 10s;
    }

    listen 443 ssl; # managed by Certbot
    ssl_certificate /etc/letsencrypt/live/skoolgpt.in/fullchain.pem; # managed by Certbot
    ssl_certificate_key /etc/letsencrypt/live/skoolgpt.in/privkey.pem; # managed by Certbot
    include /etc/letsencrypt/options-ssl-nginx.conf; # managed by Certbot
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem; # managed by Certbot
}
server {
    if ($host = be-skoolgpt.skoolgpt.in) {
        return 301 https://$host$request_uri;
    } # managed by Certbot
    listen 80;
    server_name be-skoolgpt.skoolgpt.in;
    return 404; # managed by Certbot
}
EOF

cat > "$NGX/skoolgpt.in.conf" <<'EOF'
server {
    server_name skoolgpt.in www.skoolgpt.in;

    root /usr/share/nginx/aitutor.qlass.in/public_python_aios/frontend/dist;
    index index.html;

    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;

    location / {
        try_files $uri $uri/ /index.html;
    }

    # Vite-hashed bundles are immutable; other static files (logos) rely on ?v= cache-busting.
    location ~* \.(js|css|png|jpg|jpeg|gif|ico|svg|woff|woff2)$ {
        expires 1y;
        add_header Cache-Control "public, no-transform";
        add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
    }

    listen [::]:443 ssl; # managed by Certbot
    listen 443 ssl; # managed by Certbot
    ssl_certificate /etc/letsencrypt/live/skoolgpt.in/fullchain.pem; # managed by Certbot
    ssl_certificate_key /etc/letsencrypt/live/skoolgpt.in/privkey.pem; # managed by Certbot
    include /etc/letsencrypt/options-ssl-nginx.conf; # managed by Certbot
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem; # managed by Certbot
}
server {
    if ($host = www.skoolgpt.in) {
        return 301 https://$host$request_uri;
    } # managed by Certbot
    if ($host = skoolgpt.in) {
        return 301 https://$host$request_uri;
    } # managed by Certbot
    listen 80;
    listen [::]:80;
    server_name skoolgpt.in www.skoolgpt.in;
    return 404; # managed by Certbot
}
EOF

# Legacy frontend host: permanent redirect to the new brand domain (cert kept so HTTPS redirects work).
cat > "$NGX/aitutor.qlass.in.conf" <<'EOF'
server {
    server_name aitutor.qlass.in;
    return 301 https://skoolgpt.in$request_uri;

    listen [::]:443 ssl; # managed by Certbot
    listen 443 ssl; # managed by Certbot
    ssl_certificate /etc/letsencrypt/live/aitutor.qlass.in/fullchain.pem; # managed by Certbot
    ssl_certificate_key /etc/letsencrypt/live/aitutor.qlass.in/privkey.pem; # managed by Certbot
    include /etc/letsencrypt/options-ssl-nginx.conf; # managed by Certbot
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem; # managed by Certbot
}
server {
    listen 80;
    listen [::]:80;
    server_name aitutor.qlass.in;
    return 301 https://skoolgpt.in$request_uri;
}
EOF
# be-aitutor.qlass.in is intentionally left proxying: WATI's webhook may still point at it.
nginx -t && systemctl reload nginx

log "8/9 Postgres: drop duplicate students.phone index"
sudo -u postgres psql -d aitutor2_db_prod -Atc "drop index if exists idx_students_phone;"

log "9/9 Backups + cron jobs"
mkdir -p /var/backups/skoolgpt && chown "$APP_USER:$APP_USER" /var/backups/skoolgpt && chmod 700 /var/backups/skoolgpt
chmod 755 "$APP/scripts/backup_db.sh" "$APP/scripts/deploy.sh" 2>/dev/null || true
# First backup right now, as the app user.
sudo -u "$APP_USER" "$APP/scripts/backup_db.sh"
# Merge the versioned cron fragment into the app user's crontab without touching other apps' entries.
if [ -f "$APP/scripts/crontab" ]; then
  # Drop the old hand-added nudges entry (superseded by scripts/crontab) and any previous backup line.
  existing=$(sudo -u "$APP_USER" crontab -l 2>/dev/null | grep -vE 'send_engagement_nudges\.py >> .*/logs/nudges\.log|skoolgpt-backup\.sh' || true)
  merged="$existing"
  while IFS= read -r line; do
    [ -z "$line" ] && continue
    case "$line" in \#*) continue;; esac
    grep -qF -- "$line" <<<"$existing" || merged="$merged"$'\n'"$line"
  done < "$APP/scripts/crontab"
  printf '%s\n' "$merged" | sudo -u "$APP_USER" crontab -
  echo "crontab now:"; sudo -u "$APP_USER" crontab -l | grep -E 'skoolgpt|aitutor' || true
fi

log "Done. Remaining manual items: rotate the YouTube API key; switch the WATI webhook to header auth; copy backups off-box."
