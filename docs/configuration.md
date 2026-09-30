# Configuration

Hydra is configured entirely through environment variables. Locally they live in `.env`; on a
server they live in `/etc/hydra-dns/hydra.env`, which `deploy/install.sh` creates.

`.env.example` documents every variable with a comment explaining it, and `deploy/install.sh`
shows the values used on a server. The ones worth knowing:

| Variable | Default | What it does |
|---|---|---|
| `HYDRA_PUBLIC_API_BASE` | `http://localhost:8080` | The public URL of the API. Used in every device address and setup page shown on the dashboard. |
| `HYDRA_CORS_ORIGINS` | `http://localhost:3200` | The dashboard origin(s), comma separated. Never a wildcard. |
| `HYDRA_COOKIE_DOMAIN` | — | A parent domain shared by the dashboard and API hosts, such as `.example.com`. Empty for localhost. |
| `HYDRA_LOG_HIDDEN_DOMAINS` | the `HYDRA_PUBLIC_API_BASE` host | Domains left out of the dashboard's query logs, so its own polling does not bury everything else. |
| `HYDRA_DASHBOARD_PASSWORD` | — | The dashboard password. |
| `HYDRA_SESSION_SECRET` | — | Signs session cookies. Rotating it signs everyone out. |
| `HYDRA_ADGUARD_ACCOUNTS` | — | JSON array of accounts. Seeds the account store; required on a first run. `enabled: false` on a row seeds it out of the rotation. |
| `HYDRA_ACCOUNTS_FILE` | — | Where accounts added from the dashboard live. Unset makes the list env-only and read-only in the UI. |
| `HYDRA_FALLBACK_DOH` | `https://dns.quad9.net/dns-query` | Used only when every account is spent or benched. Empty disables it, and the pool then answers SERVFAIL instead. |
| `HYDRA_DEVICES` | `default` | Seed and fallback list, `id:Display Name` comma separated |
| `HYDRA_RESOLVER_SECRET` | — | Tokens derive from it. **Must be identical on every host**, or every device address changes. |
| `HYDRA_STRATEGY` | `balanced` | `balanced` \| `round_robin` \| `quota_weighted` \| `least_used`. `balanced` serves the least-used account first so all accounts run out together. |
| `HYDRA_BALANCE_FLOOR` | `0.01` | Share of traffic the most-used account keeps while the rest catch up. `0` freezes it; above `0.02` it strands quota. |
| `HYDRA_DOT_ENABLED` | `false` | Turn on the DoT listener. Needs a certificate; off where the host cannot bind 853. |
| `HYDRA_DOT_HOST` / `_PORT` | `0.0.0.0` / `853` | Where the DoT listener binds |
| `HYDRA_DOT_BASE_DOMAIN` | — | Device hostnames are one label under this |
| `HYDRA_DOT_CERT` / `_KEY` | — | The wildcard certificate covering `*.<base domain>` |
| `HYDRA_STORE_URL` / `_KEY` / `_SECRET` | — | Device registry. Unset means `HYDRA_DEVICES` only. |
| `HYDRA_STORE_SEED` | `false` | Permission to write the initial rows into an empty registry. One host, once. |
| `HYDRA_DEVICE_CACHE` | — | On-disk mirror of the registry, read when Postgres is unreachable at startup. |
| `HYDRA_METRICS_PERSIST` / `_FLUSH` | `false` / `60` | Mirror the resolver's counters to Postgres so they survive a restart, and how often. |
| `HYDRA_CACHE_MIN_TTL` | `30` | TTL floor for ordinary answers. Raising it saves quota and slows DNS-based failover. |
| `HYDRA_CACHE_BLOCKED_MIN_TTL` | `1800` | TTL floor for answers that look blocked. Set it to `30` to disable the distinction. |
| `HYDRA_BLOCK_SOA` | `fake-for-negative-caching.adguard.com` | SOA primary names that mark an empty upstream answer as a block. Comma-separated. |
| `HYDRA_CACHE_STALE_WINDOW` | `86400` | How long past expiry an answer is kept so it can still be served when the pool is spent or failing. `0` disables serving stale. |
| `HYDRA_CACHE_FILE` | — | Where the answer cache is written so it survives a restart. Unset means the cache starts cold every time. |
| `HYDRA_CACHE_SAVE_EVERY` / `_MAX` | `300` / `10000` | How often the snapshot is written, and how many of the most recently used entries go in it. |
| `HYDRA_DDR` | `true` | Answer RFC 9462 discovery with this device's own endpoints. |
| `HYDRA_REBIND_GUARD` | `true` | Refuse an upstream answer that gives a public name a private, loopback, CGNAT or link-local address. |
| `HYDRA_REBIND_ALLOW` | `plex.direct` | Suffixes the guard lets through, matched against the name asked for. At most 64. |
| `HYDRA_CNAME_UNCLOAK` | `true` | Block an answer whose CNAME target is on a Hydra blocklist or app block that reaches the device. Adds no upstream query. |
| `HYDRA_LOCAL_ZONES` | RFC 6761 + private reverse zones | Suffixes answered here instead of upstream. Empty disables it entirely. |
| `HYDRA_LOCAL_ZONE_TTL` | `3600` | TTL on those answers, and the SOA minimum a client negative-caches them for. |
| `HYDRA_CACHE_WARM` | `50` | Top domains resolved at startup when the cache came up cold. `0` disables it. |
| `HYDRA_DEVICE_HOURLY_BUDGET` | `0` | Default ceiling on upstream queries per device per hour. `0` is off. A device can carry its own instead. Cache hits and device rules never count. |
| `HYDRA_TIMEZONE` | `UTC` | Whose clock a schedule's hours are read against. Any IANA name; an unknown one falls back to UTC. |
| `HYDRA_APP_DIR` | — | Where app blocks and the last downloaded app catalog are mirrored, read at startup before any network call. Unset keeps them in memory, and the bundled catalog stands in after a restart. |
| `HYDRA_APP_CATALOG_URL` | AdGuard's `services.json` | The app catalog to download. Empty keeps the bundled copy. |
| `HYDRA_APP_CATALOG_REFRESH` | `86400` | Seconds between catalog downloads. |
| `HYDRA_BLOCKLIST_DIR` | — | Where Hydra blocklists are mirrored (`<id>.txt` and `<id>.json`) and read from at startup, before any network call. Unset keeps them in memory only, downloaded again after a restart. |
| `HYDRA_BLOCKLIST_REFRESH` | `86400` | Seconds between refreshes. An unchanged list costs a 304; a failing one is retried every 15 minutes. |
| `HYDRA_BLOCKLIST_MAX` | `8` | Ceiling on the number of lists. Also caps their `/metrics` series. |
| `HYDRA_BLOCKLIST_MAX_NAMES` | `400000` | Ceiling on names held across every list, disabled ones included. A list that would pass it is refused whole. |
| `HYDRA_BLOCKLIST_MAX_BYTES` | `20971520` | Ceiling on one download (20 MiB), checked while it streams. |
| `HYDRA_DOQ_ENABLED` | `false` | Turn on the DoQ listener. Same certificate and base domain as DoT, on 853/udp. |
| `HYDRA_DOQ_HOST` / `_PORT` | `0.0.0.0` / `853` | Where the DoQ listener binds |
| `HYDRA_DOT_CERT_POLL` | `300` | How often both listeners check whether certbot has replaced the certificate. `systemctl reload` forces it. |
| `HYDRA_HTTP_HOST` / `_PORT` | `0.0.0.0` / `8080` | Where the API and DoH listen. The unit sets `443`. |
| `HYDRA_HTTP_CERT` / `_KEY` | falls back to `HYDRA_DOT_*` | The HTTPS certificate, reloaded in place like DoT's. |
| `HYDRA_METRICS_ENDPOINT` | `true` | Serve `/metrics` in Prometheus exposition format. A session or a machine token is needed to read it. |
| `HYDRA_METRICS_CACHE` | `30` | Seconds a rendered scrape is reused. Repeat scrapes inside the window cost nothing. |
| `HYDRA_FIRST_SEEN` | `true` | Watch for names a device has never asked for. Off frees the tracker entirely. |
| `HYDRA_FIRST_SEEN_MAX` | `2000` | Names remembered per device, least recently used evicted first. |
| `HYDRA_FIRST_SEEN_WARMUP` | `3600` | How long a device is watched before anything counts as new. |
| `HYDRA_QUIET_HOURS` | `8` | Hours without a query before a regular device is called quiet. Fractions work; `0` turns the watch off. |
| `HYDRA_QUIET_ESTABLISHED` | `16` | Distinct hours, out of the 24 before its last query, a device must have queried in to count as regular. 1 to 24. |
| `HYDRA_PRESENCE_FILE` | — | Where each device's quiet-watch rhythm and last query are saved, every five minutes and on shutdown, so the watch resumes after a restart. Unset means it starts empty and waits a whole `HYDRA_QUIET_HOURS` after every boot. |
| `HYDRA_SUSPICIOUS_SCORE` | `50` | Suspicion score, 1 to 100, at which a new name is marked suspicious, filtered by **Suspicious** and counted in the digest. |
| `HYDRA_METRICS_TOP_DEVICES` | `20` | How many device series a scrape carries. Caps cardinality so it never grows with the fleet. |
| `HYDRA_AUDIT_PERSIST` / `_FLUSH` | `false` / `120` | Mirror the audit trail to Postgres, and how often. Off keeps the last 250 changes in memory only. |
| `HYDRA_AUDIT_RETENTION_DAYS` | `365` | How long persisted audit entries are kept. |
| `HYDRA_ALERT_WEBHOOK` | — | Where attention reasons are POSTed as JSON. Empty sends nothing. |
| `HYDRA_VAPID_PUBLIC_KEY` / `_PRIVATE_KEY` | — | Web Push signing pair. Generate once; rotating the private half unsubscribes every device. |
| `HYDRA_VAPID_SUBJECT` | `mailto:admin@example.com` | Contact the push service can reach. Must be `mailto:` or `https:`. |
| `HYDRA_ALERT_EVERY` / `_REPEAT` | `600` / `21600` | How often conditions are checked, and how long a standing one waits before it is sent again. |
| `HYDRA_DIGEST` | `false` | Send a weekly summary over push. Needs the VAPID pair and at least one subscribed device. |
| `HYDRA_DIGEST_DAY` / `_HOUR` | `Mon` / `9` | When it goes, read against `HYDRA_TIMEZONE`. |
| `HYDRA_DIGEST_DEVICES` | `10` | How many devices the per-device report covers, busiest first. Clamped to 1–50. |
| `HYDRA_QUERY_RETENTION_DAYS` | `30` | How long persisted query rows are kept, on top of the existing row cap. |
| `HYDRA_EXPORT_MAX_ROWS` / `_PAGE_ROWS` | `20000` / `250` | Ceiling on an export, and the page size it streams in. |
| `HYDRA_BULK_MAX_DEVICES` | `50` | Ceiling on one bulk change or CSV import. |
| `HYDRA_HEAVY_CONCURRENCY` | `1` | How many exports may run at once. `1` keeps a large download from competing with resolving. |
| `HYDRA_POLICY_VERSIONS_KEPT` | `50` | How many policy versions are kept for rollback. |
| `HYDRA_POLICY_CHECK_S` | `900` | Seconds between comparisons of every enabled account's filtering policy. Management API reads only, no DNS allowance. `0` turns the watch off. |
| `HYDRA_LOG_JSON` | `false` | One JSON object per log line, with a request id, for a log shipper. |
| `HYDRA_INSTANCE_ID` | hostname | Labels `/metrics` and every alert, so two instances are tellable apart. |
| `HYDRA_QUOTA_WARN_DAYS` | `5` | `/api/status` turns to `attention` when the pool is projected to run out within this many days. |
| `HYDRA_ALLOW_ANONYMOUS` | `false` | Answer without a token. An open resolver gets found. |
| `HYDRA_SERVICE_NAME` | `Hydra DNS` | Name shown on the dashboard |
