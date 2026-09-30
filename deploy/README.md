# Running Hydra on a server

This is the operations runbook. The main [README](../README.md#deploy-your-own) walks through a
first install step by step; this file covers what the installer does, how to run the box, and
what tends to go wrong.

The whole app runs on one host: the DoH endpoint, the DoT and DoQ listeners, and the dashboard
API. Splitting them would put DoT traffic in one process and the dashboard in another, so every
device using Private DNS would be missing from your own stats.

## Files in this directory

| File | What it is |
|---|---|
| `install.sh` | Idempotent installer and upgrader. Safe to re-run. |
| `hydra-dns.service` | The systemd unit: unprivileged user, hardened sandbox, memory and CPU priorities. |
| `renew-hook.sh` | certbot deploy hook. It fixes certificate permissions and reloads Hydra without a restart. |

## Installer inputs

| Variable | Required | Example |
|---|---|---|
| `HYDRA_DOMAIN` | yes | `dot.example.com`. Devices use `<token>.dot.example.com`. |
| `HYDRA_API_DOMAIN` | yes | `api.dns.example.com` |
| `HYDRA_DASHBOARD_ORIGIN` | yes | `https://dns.example.com` |
| `HYDRA_COOKIE_DOMAIN` | yes | `.example.com`, a parent of both the dashboard and API hosts |
| `CF_API_TOKEN` | first run, unless a certificate already exists | Cloudflare token with *Zone → DNS → Edit* |
| `HYDRA_ADMIN_EMAIL` | no | Contact for Web Push. Defaults to `admin@<cookie domain>`. |
| `HYDRA_REPO` / `HYDRA_BRANCH` | no | Install from a fork or another branch |

```bash
sudo HYDRA_DOMAIN=dot.example.com \
     HYDRA_API_DOMAIN=api.dns.example.com \
     HYDRA_DASHBOARD_ORIGIN=https://dns.example.com \
     HYDRA_COOKIE_DOMAIN=.example.com \
     CF_API_TOKEN='…' \
     bash deploy/install.sh
```

What it does, in order:

1. Refuses to run on Python older than 3.11. Ubuntu 22.04 ships 3.10; use 24.04.
2. On hosts with under 1.5 GB of RAM and no swap, adds a 2 GB swapfile at `vm.swappiness=10`.
   On hosts under 2 GB, caps the systemd journal at 200 MB.
3. Installs Python, git, build tools and certbot with the Cloudflare plugin, and creates the
   `hydra` system user.
4. Clones the repo into `/opt/hydra-dns`, or fast-forwards it, and builds a virtualenv.
5. Issues one certificate named `hydra` covering `*.dot.example.com`, `dot.example.com` and
   `api.dns.example.com`, and installs the renewal hook.
6. Writes `/etc/hydra-dns/hydra.env` (0600) with generated `HYDRA_RESOLVER_SECRET` and
   `HYDRA_SESSION_SECRET`. On later runs it keeps every existing value and only appends settings
   that are new.
7. Opens 443/tcp, 853/tcp and 853/udp, through `ufw` if it is active and through iptables plus
   `iptables-persistent` otherwise.
8. Installs and enables the systemd unit. It starts the service only once
   `HYDRA_ADGUARD_ACCOUNTS` is filled in.

## Using a DNS provider other than Cloudflare

The installer skips certificate issuance when `/etc/letsencrypt/live/hydra` already exists. To
use another provider, issue the certificate yourself first with any certbot DNS plugin, under
the name `hydra`:

```bash
sudo apt install python3-certbot-dns-route53     # or -digitalocean, -google, -ovh, …
sudo certbot certonly --cert-name hydra --dns-route53 \
     -d '*.dot.example.com' -d dot.example.com -d api.dns.example.com
```

Then run `install.sh` without `CF_API_TOKEN`. Renewals work as normal, and the deploy hook
reloads Hydra.

## Firewalls: there are two

A cloud VM usually has a **provider firewall** (security group, NSG, VPC firewall) as well as
the **host firewall**. A port has to be open in both. A service that is perfectly healthy on the
box will look dead from outside until it is.

The symptom tells you which layer is at fault:

- **Connection refused** means the packet reached the box and nothing is listening. The service
  is down.
- **Timeout** means something dropped the packet. Check the provider firewall.

Open these in the provider firewall:

| Protocol | Port | Why |
|---|---|---|
| TCP | 22 | SSH |
| TCP | 443 | DoH, the dashboard API, setup pages |
| TCP | 853 | DoT |
| UDP | 853 | DoQ. Same port number, different protocol, so it needs its own rule. |
| ICMP | type 3 code 4 | Path MTU discovery. Without it, large answers over TLS hang silently while small ones work. |

Outbound, the host needs HTTPS (TCP 443) to AdGuard, to the blocklist URLs you subscribe to, to
Supabase if you use it, and to the browser push services.

## DNS records

All of them **DNS-only**. A proxy (Cloudflare's orange cloud) does not pass port 853 at all,
and on 443 it would terminate TLS somewhere that doesn't hold your certificate.

| Name | Type | Value |
|---|---|---|
| `api.dns.example.com` | A | server IP |
| `dot.example.com` | A | server IP |
| `*.dot.example.com` | A | server IP |
| `dns.example.com` | CNAME | your static host (for the dashboard) |

## Checking it

```bash
curl https://api.dns.example.com/api/health

kdig +tls  @<token>.dot.example.com example.com     # DoT
kdig +quic @<token>.dot.example.com example.com     # DoQ

openssl s_client -connect <token>.dot.example.com:853 \
        -servername <token>.dot.example.com </dev/null
```

`kdig` is in the `knot-dnsutils` package.

## Operating it

```bash
sudo systemctl reload hydra-dns           # re-read the certificate, no outage
sudo systemctl restart hydra-dns          # after editing hydra.env
sudo journalctl -u hydra-dns -f           # logs
sudo certbot renew --dry-run              # prove renewal works before it matters
systemd-cgtop -1                          # real memory and CPU use
cd /opt/hydra-dns && sudo bash deploy/install.sh   # upgrade
```

**A restart is a short DNS outage** on every device, because the process *is* the resolver.
Devices, accounts, rules, blocklists and rewrites are all managed from the dashboard without
one. Restart only for new code or env changes.

**An upgrade that changes `install.sh` needs running twice.** The script updates the checkout
partway through, and bash keeps executing the copy it already read. The code and unit land
correctly either way, but new settings or firewall rules only arrive on the second pass.

## State on disk

Everything lives in `/var/lib/hydra-dns/` (0700, owned by the service). The unit runs with
`ProtectSystem=strict`, so this is the only place it can write.

| File | What it is | If it is lost |
|---|---|---|
| `accounts.json` | AdGuard accounts added from the dashboard, **passwords in plain text**, 0600 | Re-enter the accounts, or restore a backup. Deliberately never mirrored to Postgres. |
| `devices.json` | Mirror of the last good registry read | Rebuilt from Postgres on the next read. |
| `cache.json` | The answer cache snapshot | Nothing, but the first minutes after a restart spend allowance on answers Hydra already had. |
| `presence.json` | Each device's activity rhythm, for quiet-device alerts | The quiet watch starts empty and waits `HYDRA_QUIET_HOURS` before alerting. |
| `blocklists/` | Downloaded Hydra blocklists and their status | Downloaded again on the next pass. |
| `apps/` | App blocks and the app catalog | Restored from the registry and the bundled catalog. |

Before you rebuild or destroy the server, take a backup from **Tools → Backup and restore** and
restore it on the new host. It includes the accounts, so keep the file as safe as `hydra.env`.

## Running on a 1 GB host

DNS resolving barely touches the CPU, but anything that scans, renders or exports does. Three
things keep a small box stable:

- **Swap** lets installs and the odd spike degrade to slow instead of being OOM-killed.
- **The unit's cgroup** (`MemoryHigh=420M`, `MemoryMax=512M`, `CPUWeight=800`,
  `OOMScoreAdjust=-500`) means memory pressure shrinks the cache before anything gets killed,
  and the resolver wins against background jobs.
- **Expensive features are off by default:** metrics/audit persistence, JSON logs, webhooks and
  the digest. Everything that stays on has a fixed ceiling, and `tests/test_footprint.py`
  enforces those ceilings.

If you turn on both metrics and audit persistence, consider `HYDRA_CACHE_MAX_ENTRIES=10000`.

## Supabase migrations

Apply `supabase/*.sql` in numeric order. Each file is cumulative and redeclares the functions it
touches. When an upgrade ships a new file, apply it **before** restarting onto the new code. If
you don't, the reads that need it fail and Hydra falls back to what it already holds on disk.

## Things that will bite

- **A proxied DNS record.** An orange cloud on `*.dot` makes Private DNS fail with no useful
  error anywhere.
- **Only one firewall opened.** See above. A timeout from outside is a firewall, not Hydra.
- **Changing `HYDRA_RESOLVER_SECRET`.** Every device address changes at once. Keep a copy.
- **Idle reclaim on free tiers.** Some providers (Oracle Always Free among them) reclaim
  instances that look idle. A resolver with real devices on it isn't idle, but an uptime monitor
  on `/api/health` keeps a quiet box busy too.
- **A certificate renewal the process never sees.** This is handled. Listeners re-check the
  certificate every `HYDRA_DOT_CERT_POLL` seconds, and the deploy hook sends a reload. Prove it
  once with `sudo certbot renew --dry-run`.
