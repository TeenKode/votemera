#!/usr/bin/env bash
# Configure TLS with an existing certificate for a dedicated Votemera LXC.
set -euo pipefail
[[ $EUID == 0 && $# == 2 ]] || { echo "Usage: bash scripts/enable-https.sh /path/fullchain.pem /path/private.key"; exit 1; }
cert=$(realpath "$1"); key=$(realpath "$2")
[[ -f "$cert" && -f "$key" && -f /etc/votemera/server.env ]]
openssl x509 -in "$cert" -noout -checkend 0
[[ $(openssl x509 -in "$cert" -pubkey -noout | openssl pkey -pubin -outform DER | sha256sum) == $(openssl pkey -in "$key" -pubout -outform DER | sha256sum) ]] || { echo 'Сертификат и ключ не совпадают'; exit 1; }
port=$(sed -n 's/^VOTEMERA_PORT=//p' /etc/votemera/server.env)
[[ "$port" =~ ^[0-9]+$ ]] && ((port>0 && port<65536 && port!=443)) || { echo 'Неверный внутренний порт'; exit 1; }
apt-get update
apt-get install -y nginx
work=$(mktemp -d); chmod 700 "$work"
cp -a /etc/votemera/server.env "$work/server.env"
conf=/etc/nginx/conf.d/votemera.conf
tls=/etc/votemera/tls
[[ ! -e "$conf" ]] || cp -a "$conf" "$work/votemera.conf"
[[ ! -e "$tls" ]] || cp -a "$tls" "$work/tls"
rollback(){
  rc=$?
  if ((rc)); then
    cp -a "$work/server.env" /etc/votemera/server.env
    rm -f "$conf"; [[ ! -f "$work/votemera.conf" ]] || cp -a "$work/votemera.conf" "$conf"
    rm -rf "$tls"; [[ ! -d "$work/tls" ]] || cp -a "$work/tls" "$tls"
    systemctl restart votemera || true
    nginx -t && systemctl reload nginx || true
  fi
  rm -rf "$work"; exit "$rc"
}
trap rollback EXIT
install -d -o root -g root -m 0700 "$tls"
install -o root -g root -m 0644 "$cert" "$tls/fullchain.pem"
install -o root -g root -m 0600 "$key" "$tls/key.pem"
cat > "$conf" <<CONF
server {
    listen 443 ssl;
    server_name _;
    ssl_certificate /etc/votemera/tls/fullchain.pem;
    ssl_certificate_key /etc/votemera/tls/key.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    client_max_body_size 64m;
    location / {
        proxy_pass http://127.0.0.1:$port;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Forwarded-For \$remote_addr;
        proxy_set_header X-Forwarded-Proto https;
        proxy_buffering off;
        proxy_read_timeout 3600s;
    }
}
CONF
nginx -t
sed -i 's/^VOTEMERA_HOST=.*/VOTEMERA_HOST=127.0.0.1/' /etc/votemera/server.env
systemctl restart votemera
for i in {1..20}; do curl -fsS "http://127.0.0.1:$port/health" && break; sleep 1; done
curl -fsS "http://127.0.0.1:$port/health" >/dev/null
systemctl enable --now nginx
systemctl reload nginx
# Local transport check only; clients still validate the certificate normally.
curl -kfsS https://127.0.0.1/health >/dev/null
echo 'HTTPS настроен. Используйте имя из сертификата: https://<имя>/{admin,vote,display}'
