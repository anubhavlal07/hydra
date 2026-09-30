# HTTP API

Every route the resolver serves. *cookie* means a dashboard session; *token* on `/metrics` means a
machine token minted from the Tools page and sent as `Authorization: Bearer <token>`.

| Route | Auth | Purpose |
|---|---|---|
| `GET\|POST /dns-query/{token}` | device token | RFC 8484 wire format |
| `GET /dns-query/{token}?name=&type=` | device token | JSON, dns.google-compatible |
| *(TLS SNI)* `{token}.dot.…:853` | device token | RFC 7858 DNS-over-TLS |
| *(QUIC SNI)* `{token}.dot.…:853/udp` | device token | RFC 9250 DNS-over-QUIC |
| `GET /setup/{token}` | device token | Setup page for the device itself: profile, Private DNS hostname, DoH URL |
| `GET /setup/{token}/hydra.mobileconfig` | device token | Apple configuration profile |
| `GET /api/health` | none | `ok` \| `degraded` \| `starting` \| `unconfigured`, plus uptime. `degraded` also covers a silent listener. Detail needs a session. |
| `GET /api/status` | none | `nominal` \| `attention` — the early-warning monitor. Reasons need a session. |
| `GET /api/overview` | cookie | Quota rollup, resolver summary, upstream and pool health, quota saved this month |
| `GET /api/accounts` | cookie | Per-account auth state, quota, device, store status |
| `POST /api/accounts` | cookie | Add an account, after proving the sign-in. `policy_sync` reports the pool's policy being written to it |
| `PATCH /api/accounts/{label}` | cookie | Take an account out of the rotation, or put it back |
| `DELETE /api/accounts/{label}` | cookie | Remove an account from the pool |
| `GET /api/policy` | cookie | Pooled filtering policy and every disagreement between accounts |
| `PUT /api/policy` | cookie | Write rules, AdGuard blocklists, either master switch, protection, safe browsing, parental control to every account |
| `POST /api/policy/rule` | cookie | Block, allow or clear one domain everywhere |
| `POST /api/policy/sync` | cookie | Write the majority's settings and the union of rules to every account. Returns `repaired` and `left` |
| `GET /api/resolver`, `/api/resolver/recent` | cookie | Live metrics, timeline, per-device, top domains, query log. `recent` takes `device` and `result=blocked\|allowed\|error`, and returns the device registry alongside the rows |
| `GET /api/stats/*`, `/api/query-log` | cookie | AdGuard data, merged across accounts |
| `GET /api/devices` | cookie | Devices, both addresses, their traffic, their rules |
| `PUT /api/devices/{id}/rules` | cookie | Per-device blocking, applied inside the resolver |
| `PUT /api/devices/{id}/policy` | cookie | Safe search and what a block answers with, per device |
| `GET /api/resolver/history` | cookie | Query log out of Postgres: paged, searchable, survives a restart |
| `GET|PUT|DELETE /api/rewrites` | cookie | Names answered locally, before the cache |
| `POST\|PATCH\|DELETE /api/devices` | cookie | Add, rename, revoke |
| `GET /api/native-dns` | cookie | Per-account AdGuard endpoints, as an unpooled fallback |
| `POST /api/lookup` | cookie | Resolve through the real path |
| `POST /api/cache/clear` | cookie | Drop every cached answer |
| `GET /metrics` | cookie **or** token | Prometheus exposition. Bounded cardinality, cached for `HYDRA_METRICS_CACHE` seconds. |
| `GET /api/audit` | cookie | Who changed what. `source=live\|history`, filtered by actor, action, outcome or text |
| `GET /api/tokens` | cookie | Machine tokens, by name and scope. Never the secret. |
| `POST /api/tokens` | cookie | Mint one. The secret is shown once and only its hash is stored. |
| `DELETE /api/tokens/{id}` | cookie | Revoke one |
| `GET /api/push` | cookie | VAPID public key and the devices subscribed |
| `POST /api/push/subscribe` | cookie | Register a browser subscription |
| `POST /api/push/unsubscribe` | cookie | Drop one |
| `POST /api/push/test` | cookie | Send a test notification to every subscribed device |
| `GET /api/alerts` | cookie | Whether a webhook is set, what is firing, what has been delivered |
| `POST /api/alerts/test` | cookie | Send a synthetic alert to prove the webhook works |
| `GET /api/digest/devices` | cookie | The digest's per-device report: new names, top blocked names and the layer blocking each. Resolves nothing |
| `POST /api/policy/preview` | cookie | What a policy write would change, read against the live accounts, without writing |
| `GET /api/policy/versions` | cookie | Every recorded policy write, each diffed against what is live now |
| `POST /api/policy/versions/{id}/restore` | cookie | Put a previous policy back |
| `GET /api/query-log/export` | cookie | Stream the log as `csv` or `ndjson`, from the live buffer or from Postgres |
| `DELETE /api/query-log?device=` | cookie | Forget one device's persisted history |
| `PUT /api/devices/{id}/tags` | cookie | Tag a device |
| `POST /api/devices/bulk` | cookie | Apply tags, rules or a policy to many devices in one pass |
| `GET /api/devices/export` | cookie | Devices as CSV, in the shape the importer reads |
| `POST /api/devices/import` | cookie | Add or update devices from CSV. `dry_run` rehearses it |
| `POST /api/devices/{id}/rules/entry` | cookie | Block, allow or clear one domain on one device. `{domain, action: block\|allow\|clear, minutes?, temporary?}`, read-modify-write under the store's lock. `minutes` (5 to 10080) makes the entry temporary; `temporary: true` limits a `clear` to the temporary entry |
| `POST /api/rule-groups/{id}/rules/entry` | cookie | The same on one schedule's rules, every other field kept as stored |
| `GET /api/domain?name=` | cookie | The domain sheet: each device's deciding layer, who asks, the cache peek, the Hydra lists naming it. Resolves nothing |
| `GET /api/blocklists` | cookie | Hydra blocklists with their status, `editable`, the tags devices carry, each list's `reaches`, and its `overlap` and `unique` counts |
| `GET /api/blocklists/presets` | cookie | The preset catalog, each entry with whether it `fits` the remaining name headroom and whether it is already `subscribed` |
| `PUT /api/blocklists/{id}` | cookie | Create or update a list's `name`, `url`, `tags`, `allow` and `enabled`. A new URL is fetched at once |
| `POST /api/blocklists/{id}/refresh` | cookie | Fetch now; the response carries the new status |
| `POST /api/blocklists/{id}/allow` | cookie | `{domain, action: add\|remove}` on the list's exceptions, read-modify-write under the store's lock |
| `DELETE /api/blocklists/{id}` | cookie | Remove a list, its files and its set |
| `GET /api/apps` | cookie | App blocks, with who they reach, whether they are in force, their hits and who was seen using them, plus the apps seen on the network |
| `GET /api/apps/catalog` | cookie | The app catalog: id, name, category, icon and domain count per app. Has an ETag |
| `PUT /api/apps/{id}` | cookie | `{tags, days, starts_at, ends_at, enabled}`: block an app, or change its block |
| `DELETE /api/apps/{id}` | cookie | Unblock an app |
| `GET /api/backup` | cookie | Devices, rules, rewrites, Hydra blocklist settings and accounts as one document. `?accounts=false` leaves the passwords out |
| `POST /api/restore` | cookie | Put a backup back. Additive, idempotent, and proves every account before storing it |
