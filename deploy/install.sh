#!/usr/bin/env bash

set -euo pipefail

REPO="${HYDRA_REPO:-https://github.com/anubhavlal07/hydra.git}"
BRANCH="${HYDRA_BRANCH:-main}"
DOT_DOMAIN="${HYDRA_DOMAIN:-}"
API_DOMAIN="${HYDRA_API_DOMAIN:-}"
DASHBOARD_ORIGIN="${HYDRA_DASHBOARD_ORIGIN:-}"
COOKIE_DOMAIN="${HYDRA_COOKIE_DOMAIN:-}"
ADMIN_EMAIL="${HYDRA_ADMIN_EMAIL:-}"
APP_DIR=/opt/hydra-dns
ENV_DIR=/etc/hydra-dns
CERT_NAME=hydra

log() { printf '\n\033[1;34m==>\033[0m %s\n' "$*"; }
die() { printf '\n\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "run with sudo"
[[ -n "$DOT_DOMAIN" ]] || die "set HYDRA_DOMAIN, the DoT base domain, e.g. dot.example.com"
[[ -n "$API_DOMAIN" ]] || die "set HYDRA_API_DOMAIN, the API and DoH host, e.g. api.dns.example.com"
[[ -n "$DASHBOARD_ORIGIN" ]] || die "set HYDRA_DASHBOARD_ORIGIN, where the dashboard is served, e.g. https://dns.example.com"
[[ -n "$COOKIE_DOMAIN" ]] || die "set HYDRA_COOKIE_DOMAIN, the parent both hosts share, e.g. .example.com"
[[ -n "$ADMIN_EMAIL" ]] || ADMIN_EMAIL="admin@${COOKIE_DOMAIN#.}"

if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
    FIREWALL=ufw
else
    FIREWALL=iptables
fi

PY_OK=$(python3 -c 'import sys; print(1 if sys.version_info >= (3, 11) else 0)' 2>/dev/null || echo 0)
if [[ "$PY_OK" != "1" ]]; then
    die "Python 3.11+ is required (found $(python3 -V 2>&1). Use the Canonical Ubuntu 24.04 image, which ships 3.12."
fi

TOTAL_MB=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)
SWAP_MB=$(awk '/SwapTotal/ {print int($2/1024)}' /proc/meminfo)

if [[ "$TOTAL_MB" -lt 1536 && "$SWAP_MB" -lt 512 ]]; then
    log "Adding a 2G swapfile (${TOTAL_MB}MB of RAM, ${SWAP_MB}MB of swap)"
    if [[ ! -f /swapfile ]]; then
        fallocate -l 2G /swapfile || dd if=/dev/zero of=/swapfile bs=1M count=2048 status=none
        chmod 600 /swapfile
        mkswap /swapfile >/dev/null
    fi
    swapon /swapfile 2>/dev/null || true
    grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
    sysctl -qw vm.swappiness=10
    grep -q '^vm.swappiness' /etc/sysctl.d/99-hydra.conf 2>/dev/null \
        || echo 'vm.swappiness=10' >> /etc/sysctl.d/99-hydra.conf
fi

if [[ "$TOTAL_MB" -lt 2048 && ! -f /etc/systemd/journald.conf.d/99-hydra.conf ]]; then
    log "Capping the journal (it was the largest resident process on this host)"
    mkdir -p /etc/systemd/journald.conf.d
    cat > /etc/systemd/journald.conf.d/99-hydra.conf <<'JOURNALD'
[Journal]
SystemMaxUse=200M
SystemMaxFileSize=20M
RuntimeMaxUse=32M
Compress=yes
JOURNALD
    systemctl restart systemd-journald
    journalctl --vacuum-size=200M >/dev/null 2>&1 || true
fi

log "Installing packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq \
    python3 python3-venv python3-dev git build-essential \
    certbot python3-certbot-dns-cloudflare >/dev/null

if [[ "$FIREWALL" == iptables ]]; then
    apt-get install -y -qq iptables-persistent >/dev/null
fi

if ! id hydra &>/dev/null; then
    log "Creating the hydra service user"
    useradd --system --no-create-home --shell /usr/sbin/nologin hydra
fi

log "Fetching the application"
git config --global --add safe.directory "$APP_DIR" 2>/dev/null || true

if [[ -d "$APP_DIR/.git" ]]; then
    git -C "$APP_DIR" fetch --quiet origin "$BRANCH"
    git -C "$APP_DIR" reset --hard --quiet "origin/$BRANCH"
else
    rm -rf "$APP_DIR"
    git clone --quiet --branch "$BRANCH" --depth 1 "$REPO" "$APP_DIR"
fi

log "Building the virtualenv"
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --quiet --upgrade pip
"$APP_DIR/.venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"
chown -R hydra:hydra "$APP_DIR"

mkdir -p "$ENV_DIR"
chmod 700 "$ENV_DIR"

if [[ ! -d "/etc/letsencrypt/live/$CERT_NAME" ]]; then
    log "Requesting the certificate for *.$DOT_DOMAIN and $API_DOMAIN"
    [[ -n "${CF_API_TOKEN:-}" ]] || die "CF_API_TOKEN is required to issue the wildcard certificate, or issue one named '$CERT_NAME' yourself first"

    install -m 600 /dev/null "$ENV_DIR/cloudflare.ini"
    printf 'dns_cloudflare_api_token = %s\n' "$CF_API_TOKEN" > "$ENV_DIR/cloudflare.ini"

    certbot certonly \
        --non-interactive --agree-tos --register-unsafely-without-email \
        --cert-name "$CERT_NAME" \
        --dns-cloudflare \
        --dns-cloudflare-credentials "$ENV_DIR/cloudflare.ini" \
        --dns-cloudflare-propagation-seconds 30 \
        -d "*.$DOT_DOMAIN" -d "$DOT_DOMAIN" -d "$API_DOMAIN"
else
    log "Certificate already present, leaving it alone"
fi

chgrp -R hydra /etc/letsencrypt/live /etc/letsencrypt/archive
chmod g+rx /etc/letsencrypt/live /etc/letsencrypt/archive
chmod -R g+rX "/etc/letsencrypt/archive/$CERT_NAME"

log "Installing the renewal hook"
install -m 755 "$APP_DIR/deploy/renew-hook.sh" /etc/letsencrypt/renewal-hooks/deploy/hydra-dns.sh

if [[ ! -f "$ENV_DIR/hydra.env" ]]; then
    log "Writing a template environment file"
    install -m 600 /dev/null "$ENV_DIR/hydra.env"
    cat > "$ENV_DIR/hydra.env" <<EOF
# Hydra DNS. Fill in the secrets, then: systemctl restart hydra-dns

HYDRA_PUBLIC_API_BASE=https://$API_DOMAIN
HYDRA_CORS_ORIGINS=$DASHBOARD_ORIGIN
HYDRA_COOKIE_DOMAIN=$COOKIE_DOMAIN

# Generated once on this host. Every device URL is derived from it, so keep it stable.
HYDRA_RESOLVER_SECRET=
HYDRA_SESSION_SECRET=
HYDRA_DASHBOARD_PASSWORD=
HYDRA_ADGUARD_ACCOUNTS=

HYDRA_DEVICES=laptop:Laptop,phone:Phone
HYDRA_STRATEGY=balanced

HYDRA_STORE_URL=
HYDRA_STORE_KEY=
HYDRA_STORE_SECRET=

# Where the last successfully read device list is mirrored. Read at startup when Postgres
# cannot be reached, so a restart during an outage serves the devices you actually have
# rather than whatever HYDRA_DEVICES was last hand-edited to. systemd creates and owns this
# directory via StateDirectory=.
HYDRA_DEVICE_CACHE=/var/lib/hydra-dns/devices.json
# Where accounts added from the dashboard live. The value above stays the seed: it fills this
# file on first run and is the fallback if it ever becomes unreadable. Same directory, same
# 0700 StateDirectory, and the file itself is written 0600 -- it holds AdGuard passwords.
HYDRA_ACCOUNTS_FILE=/var/lib/hydra-dns/accounts.json

# Mirror the resolver's counters to Postgres on a timer, so a restart no longer wipes the
# dashboard's live view. Needs 002_metrics_persistence.sql applied.
HYDRA_METRICS_PERSIST=false
HYDRA_METRICS_FLUSH=60
# Only for a first-time setup against an empty registry. Leave unset otherwise: it would let
# this host overwrite real device names from HYDRA_DEVICES above.
# HYDRA_STORE_SEED=true

# DNS-over-TLS, for Android Private DNS and routers.
HYDRA_DOT_ENABLED=true
HYDRA_DOT_PORT=853
HYDRA_DOT_BASE_DOMAIN=$DOT_DOMAIN
HYDRA_DOT_CERT=/etc/letsencrypt/live/$CERT_NAME/fullchain.pem
HYDRA_DOT_KEY=/etc/letsencrypt/live/$CERT_NAME/privkey.pem
# How often both listeners check whether certbot has replaced the certificate under them.
# A renewal is picked up in place, so it is no longer a restart and no longer an outage.
# "systemctl reload hydra-dns" forces the same check immediately.
HYDRA_DOT_CERT_POLL=300

# The HTTPS listener. It serves the same wildcard certificate as DoT unless told otherwise,
# and reloads it in place for the same reason.
HYDRA_HTTP_PORT=443

# DNS-over-QUIC, the same identity over UDP. Needs 853/udp open in both firewalls.
HYDRA_DOQ_ENABLED=true
HYDRA_DOQ_PORT=853

# The answer cache, written here on a timer and on shutdown and read back at startup. A
# restart used to throw away every cached answer, and every one of those is a query that then
# has to be bought back from an account's monthly allowance.
HYDRA_CACHE_FILE=/var/lib/hydra-dns/cache.json
HYDRA_CACHE_SAVE_EVERY=300

# How long past expiry an answer is kept so it can still be served when every account is spent
# or unreachable. A day-old answer beats resolving unfiltered, and it costs no allowance.
HYDRA_CACHE_STALE_WINDOW=86400

# A ceiling on upstream queries per device per hour, so one misbehaving device cannot drain
# the pooled allowance. Cache hits, device rules and rewrites are free and never counted.
# 0 disables it.
HYDRA_DEVICE_HOURLY_BUDGET=0
HYDRA_TIMEZONE=UTC
EOF
    secret() { python3 -c 'import secrets; print(secrets.token_urlsafe(32))'; }
    sed -i "s|^HYDRA_RESOLVER_SECRET=$|HYDRA_RESOLVER_SECRET=$(secret)|; s|^HYDRA_SESSION_SECRET=$|HYDRA_SESSION_SECRET=$(secret)|" "$ENV_DIR/hydra.env"
    echo "    -> edit $ENV_DIR/hydra.env before starting"
else
    log "Environment file already present, keeping every value in it"
fi

ensure_setting() {
    local key=$1 value=$2 note=$3
    if grep -q "^[#[:space:]]*${key}=" "$ENV_DIR/hydra.env"; then
        return 0
    fi
    {
        echo ""
        echo "# $note"
        echo "${key}=${value}"
    } >> "$ENV_DIR/hydra.env"
    echo "    added ${key}=${value}"
}

log "Filling in settings this build expects"
ensure_setting HYDRA_CACHE_FILE /var/lib/hydra-dns/cache.json     "The answer cache, so a restart no longer starts cold and re-buys what it already knew."
ensure_setting HYDRA_CACHE_SAVE_EVERY 300     "How often that snapshot is written. It is also written on shutdown."
ensure_setting HYDRA_DOT_CERT_POLL 300     "How often both listeners check for a renewed certificate. Reloaded in place, never a restart."
ensure_setting HYDRA_DOQ_ENABLED true     "DNS-over-QUIC on 853/udp. Same certificate, same token-in-SNI, one stream per query."
ensure_setting HYDRA_DOQ_PORT 853     "Same number as DoT, different protocol, separate firewall rule."
ensure_setting HYDRA_DEVICE_HOURLY_BUDGET 0     "Upstream queries per device per hour. 0 is off; set it above the noisiest device's worst hour."
ensure_setting HYDRA_METRICS_ENDPOINT true     "Prometheus exposition at /metrics. Needs a session or a machine token; it is not public."
ensure_setting HYDRA_AUDIT_PERSIST false     "Mirror the audit trail to Postgres. Off keeps the last 250 changes in memory only, and a restart clears them. Needs 005_audit_log.sql."
ensure_setting HYDRA_AUDIT_RETENTION_DAYS 365     "How long persisted audit entries are kept."
ensure_setting HYDRA_ALERT_WEBHOOK ""     "Where attention reasons are POSTed as JSON. Empty sends nothing. A standing reason is re-sent only after HYDRA_ALERT_REPEAT."
ensure_setting HYDRA_QUERY_RETENTION_DAYS 30     "How long persisted query rows are kept, on top of the existing row cap."
ensure_setting HYDRA_VAPID_PUBLIC_KEY ""     "Web Push. Generate a pair with: .venv/bin/python -c 'from hydra_dns import webpush; print(*webpush.generate_keys(), sep=chr(10))'"
ensure_setting HYDRA_VAPID_PRIVATE_KEY ""     "The private half of the pair above. Rotating it unsubscribes every device."
ensure_setting HYDRA_VAPID_SUBJECT "mailto:$ADMIN_EMAIL"     "Contact the push service can reach if it needs to. Must be a mailto: or https: URL."
ensure_setting HYDRA_TIMEZONE UTC     "Whose clock a schedule's hours are read against. Any IANA name, such as Asia/Kolkata. An unknown one falls back to UTC."
ensure_setting HYDRA_LOG_JSON false     "One JSON object per log line with a request id, for a log shipper. Leave off when a human reads the journal."
ensure_setting HYDRA_DDR true     "Answer RFC 9462 discovery. A client asks where its encrypted endpoints are and is told its own; DoT and DoQ appear only once their listeners are actually up."
ensure_setting HYDRA_REBIND_GUARD true     "Refuse an upstream answer that points a public name at a private, loopback, CGNAT or link-local address. Stops DNS rebinding."
ensure_setting HYDRA_REBIND_ALLOW plex.direct     "Suffixes the rebind guard lets through, comma separated, matched against the name asked for."
ensure_setting HYDRA_FIRST_SEEN true     "Collect names a device has never asked for before, on the log's New tab. Bounded per device, and quiet for each device's first hour."
ensure_setting HYDRA_DIGEST false     "A weekly summary over push: queries, block and cache rates, the busiest device, allowance left. Needs the VAPID pair above and a subscribed device."
ensure_setting HYDRA_DIGEST_DAY Mon     "Which day the digest goes, read against HYDRA_TIMEZONE."
ensure_setting HYDRA_DIGEST_HOUR 9     "Which hour it goes, 0-23, on that day."
ensure_setting HYDRA_DIGEST_DEVICES 10     "How many devices the digest's per-device report covers, busiest first, 1-50."
ensure_setting HYDRA_BLOCKLIST_DIR /var/lib/hydra-dns/blocklists     "Hydra blocklists on disk, read at startup before any network call. Must stay inside the StateDirectory, the only place the unit can write. Needs 014_blocklists.sql."
ensure_setting HYDRA_BLOCKLIST_REFRESH 86400     "Seconds between blocklist refreshes. An unchanged list costs a 304; a failing one is retried every 15 minutes."
ensure_setting HYDRA_BLOCKLIST_MAX 8     "Ceiling on the number of Hydra blocklists."
ensure_setting HYDRA_BLOCKLIST_MAX_NAMES 400000     "Ceiling on names held across every list, disabled ones included. About 42 MB at this value."
ensure_setting HYDRA_BLOCKLIST_MAX_BYTES 20971520     "Ceiling on one list download, 20 MiB."
ensure_setting HYDRA_APP_DIR /var/lib/hydra-dns/apps     "App blocks and the downloaded app catalog on disk, read at startup before any network call. Must stay inside the StateDirectory. Needs 015_app_blocks.sql."
ensure_setting HYDRA_PRESENCE_FILE /var/lib/hydra-dns/presence.json     "Each device's quiet-watch rhythm, so a restart resumes it instead of waiting a whole HYDRA_QUIET_HOURS. Must stay inside the StateDirectory."
ensure_setting HYDRA_APP_CATALOG_REFRESH 86400    "Seconds between app catalog refreshes. An unchanged catalog costs a 304; a failing one is retried every 15 minutes."


log "Opening 443/tcp, 853/tcp and 853/udp on the host firewall ($FIREWALL)"

if [[ "$FIREWALL" == ufw ]]; then
    ufw allow 443/tcp >/dev/null
    ufw allow 853/tcp >/dev/null
    ufw allow 853/udp >/dev/null
else
    open_port() {
        local port=$1
        if iptables -C INPUT -p tcp --dport "$port" -m state --state NEW -j ACCEPT 2>/dev/null; then
            echo "    $port already open"
            return 0
        fi

        local pos
        pos=$(iptables -L INPUT --line-numbers -n 2>/dev/null \
            | awk '$1 ~ /^[0-9]+$/ && ($2 == "REJECT" || $2 == "DROP") { print $1; exit }')

        if [[ -n "$pos" ]]; then
            iptables -I INPUT "$pos" -p tcp --dport "$port" -m state --state NEW -j ACCEPT
            echo "    $port opened (inserted at position $pos, before the catch-all)"
        else
            iptables -A INPUT -p tcp --dport "$port" -m state --state NEW -j ACCEPT
            echo "    $port opened (appended; no catch-all rule found)"
        fi
    }

    for port in 443 853; do
        open_port "$port"
    done

    open_udp_port() {
        local port=$1
        if iptables -C INPUT -p udp --dport "$port" -m state --state NEW -j ACCEPT 2>/dev/null; then
            echo "    $port/udp already open"
            return 0
        fi

        local pos
        pos=$(iptables -L INPUT --line-numbers -n 2>/dev/null         | awk '$1 ~ /^[0-9]+$/ && ($2 == "REJECT" || $2 == "DROP") { print $1; exit }')

        if [[ -n "$pos" ]]; then
            iptables -I INPUT "$pos" -p udp --dport "$port" -m state --state NEW -j ACCEPT
            echo "    $port/udp opened (inserted at position $pos, before the catch-all)"
        else
            iptables -A INPUT -p udp --dport "$port" -m state --state NEW -j ACCEPT
            echo "    $port/udp opened (appended; no catch-all rule found)"
        fi
    }

    open_udp_port 853

    for port in 443 853; do
        iptables -C INPUT -p tcp --dport "$port" -m state --state NEW -j ACCEPT 2>/dev/null \
            || die "failed to open port $port on the host firewall"
    done

    netfilter-persistent save >/dev/null 2>&1 || iptables-save > /etc/iptables/rules.v4
fi

log "Installing the systemd unit"
install -m 644 "$APP_DIR/deploy/hydra-dns.service" /etc/systemd/system/hydra-dns.service
systemctl daemon-reload
systemctl enable hydra-dns >/dev/null

if grep -q '^HYDRA_ADGUARD_ACCOUNTS=$' "$ENV_DIR/hydra.env"; then
    cat <<EOF

  Not starting yet: $ENV_DIR/hydra.env still has empty secrets.

  Fill them in, then:
      sudo systemctl restart hydra-dns
      sudo systemctl status hydra-dns
EOF
else
    log "Restarting"
    systemctl restart hydra-dns
    sleep 3
    systemctl --no-pager --lines=20 status hydra-dns || true
fi

cat <<EOF

Done. Remaining steps that cannot be done from this box:

  1. Your provider's cloud firewall (security group, NSG, firewall rules), if it has one:
         TCP 22, TCP 443, TCP 853, UDP 853   from 0.0.0.0/0
     A port missing there times out from off-box rather than being refused.

  2. DNS, all DNS-only (grey cloud on Cloudflare) -- proxying breaks DoT entirely and
     terminates TLS for HTTPS, which is not wanted here:
         $DOT_DOMAIN       A  -> this VM's public IP
         *.$DOT_DOMAIN     A  -> this VM's public IP
         $API_DOMAIN       A  -> this VM's public IP

  3. Check it from anywhere:
         kdig +tls @$DOT_DOMAIN -d @<token>.$DOT_DOMAIN example.com
         curl https://$API_DOMAIN/api/health

EOF
