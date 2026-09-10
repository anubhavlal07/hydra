import type {
  AppCatalog,
  AppsResponse,
  AccountsResponse,
  AlertStatus,
  BlocklistPresetsResponse,
  BlocklistsResponse,
  DigestDevicesResponse,
  DigestStatus,
  DevicesResponse,
  DomainResponse,
  FirstSeenResponse,
  TokenStoreStatus,
  Health,
  HistoryResponse,
  LogDevice,
  LookupResult,
  Overview,
  PolicyResponse,
  RecentQueries,
  ResolverDetail,
  RewritesResponse,
  RuleGroupsResponse,
  StatsTimeline,
  UpstreamSnapshot,
} from "@/lib/types";

export const upstream = (label: string, over: Partial<UpstreamSnapshot> = {}): UpstreamSnapshot => ({
  label,
  configured: true,
  enabled: true,
  available: true,
  benched: false,
  benched_for_s: 0,
  exhausted: false,
  queries: 1200,
  errors: 0,
  error_rate: 0,
  latency_ms: 24,
  quota_used: 30_000,
  quota_limit: 300_000,
  quota_remaining: 270_000,
  quota_fraction: 0.1,
  share: 0.5,
  deficit: 0,
  last_error: "",
  last_used_at: 1_700_000_000,
  ...over,
});

export const overview = (over: Partial<Overview> = {}): Overview => ({
  service: "hydra",
  resolver_url: "https://api.dns.example",
  strategy: "balanced",
  warnings: [],
  quota: {
    accounts_reporting: 2,
    accounts_disabled: 0,
    requests_limit: 600_000,
    requests_used: 60_000,
    requests_remaining: 540_000,
    headroom_days: 90,
    used_fraction: 0.1,
    cache_hit_rate: 0.42,
    cache_multiplier: 1.7,
    effective_remaining: 918_000,
    effective_limit: 1_020_000,
  },
  resolver: {
    queries: 48_210,
    cached: 20_000,
    blocked: 17_700,
    errors: 12,
    refused: 3,
    cache_hit_rate: 0.42,
    block_rate: 0.367,
    uptime_s: 93_600,
    latency: { p50: 18, p95: 61, p99: 140 },
  },
  cache: {
    entries: 4210,
    capacity: 20_000,
    hits: 20_000,
    misses: 28_210,
    hit_rate: 0.42,
    evictions: 12,
    expired: 300,
    live: 3900,
    stale: 310,
    stale_hits: 1842,
    restored: 2600,
    stale_window_s: 86_400,
  },
  accounts: {
    total: 2,
    enabled: 2,
    authenticated: 2,
    serving: 2,
    discovery_done: true,
    last_refresh_at: 1_700_000_000,
  },
  upstreams: [upstream("alpha"), upstream("beta", { share: 0.5 })],
  pool: {
    ready: true,
    spent: false,
    configured: 2,
    available: 2,
    balance: {
      strategy: "balanced",
      balancing: true,
      floor: 0.05,
      reporting: 2,
      target_fraction: 0.12,
      spread: 0.03,
      balanced: false,
      shares: { alpha: 0.5, beta: 0.5 },
    },
    fallback: null,
  },
  attention: [],
  certificate: { known: true, expires_at: 1_800_000_000, days_left: 44 },
  prefetch: { enabled: true, renewed: 812, failed: 2, min_hits: 3, window_s: 300, warmed: 640 },
  devices: 3,
  saved: {
    month: "2026-09",
    since: 1_788_220_800,
    until: 1_790_812_800,
    by_cause: { blocklist: 3_100, rule: 420, cache: 9_600, coalesced: 172 },
    gross: 13_292,
    prefetch_cost: 812,
    net: 12_480,
    allowance: 600_000,
    fraction: 0.0208,
    partial: false,
  },
  ...over,
});

export const resolverDetail = (over: Partial<ResolverDetail> = {}): ResolverDetail => ({
  summary: overview().resolver,
  timeline: [
    { at: 1_700_000_000, queries: 120, blocked: 40, cached: 60 },
    { at: 1_700_000_600, queries: 180, blocked: 55, cached: 80 },
    { at: 1_700_001_200, queries: 90, blocked: 20, cached: 40 },
  ],
  devices: [],
  top_domains: [
    { domain: "graph.facebook.com", queries: 512, blocked: 500 },
    { domain: "example.com", queries: 128, blocked: 0 },
  ],
  top_blocked: [{ domain: "doubleclick.net", queries: 310, blocked: 310 }],
  qtypes: [{ type: "A", queries: 900 }],
  upstreams: [upstream("alpha")],
  cache: overview().cache,
  ...over,
});

export const statsTimeline = (over: Partial<StatsTimeline> = {}): StatsTimeline => ({
  window: "24h",
  series: [
    { at: 1_700_000_000_000, queries: 400, blocked: 140 },
    { at: 1_700_003_600_000, queries: 620, blocked: 210 },
  ],
  totals: { queries: 1020, blocked: 350 },
  per_account: { alpha: 600, beta: 420 },
  errors: {},
  ...over,
});

export const health = (over: Partial<Health> = {}): Health => ({
  status: "ok",
  uptime_s: 93_600,
  upstreams_ready: 2,
  accounts: 2,
  discovery_done: true,
  attention: [],
  devices: 3,
  warnings: [],
  dot: { enabled: true, queries: 8100, open_connections: 4 },
  certificate_watch: {
    watching: true,
    listeners: ["dot", "api"],
    poll_s: 3600,
    reloads: 2,
    failures: 0,
    last_reload_at: 1_700_000_000,
    last_error: "",
  },
  cache_snapshot: {
    enabled: true,
    path: "/var/lib/hydra/cache.json",
    restored: 2600,
    saved: 9,
    last_save_at: 1_700_000_000,
    last_error: "",
  },
  budget: {
    enabled: true,
    hourly: 2000,
    denied: 14,
    devices: [
      { device: "laptop", hourly: 2000, remaining: 1400, used: 600, denied: 0, breached_at: null },
      {
        device: "tv",
        hourly: 500,
        remaining: 0,
        used: 500,
        denied: 14,
        breached_at: 1_700_000_000,
      },
    ],
  },
  coalescing: { shared: 3120, led: 2800, in_flight: 2 },
  quiet: {
    quiet_s: 28_800,
    established_hours: 16,
    tracked: 3,
    established: 2,
    settled: true,
    settles_at: 1_700_000_000,
  },
  rebind: { enabled: true, allow: ["plex.direct"], refused: 1_205, failed: 0 },
  cname: { enabled: true, blocked: 318, failed: 0 },
  blocklists: {
    lists: 3,
    enabled: 3,
    loaded: 2,
    failing: 1,
    names: 184_210,
    hits: 7_415,
    max_lists: 16,
    max_names: 1_000_000,
    refresh_seconds: 86_400,
    on_disk: true,
    last_sync_at: 1_700_000_000,
    last_error: "",
  },
  ...over,
});

export const logDevices: LogDevice[] = [
  { id: "laptop", name: "Work Laptop", registered: true, queries: 4200, blocked: 900, last_seen: 1_700_000_000 },
  { id: "pixel", name: "Pixel 9", registered: true, queries: 1900, blocked: 400, last_seen: 1_700_000_000 },
  { id: "tv", name: "Living room TV", registered: true, queries: 0, blocked: 0, last_seen: null },
  { id: "anonymous", name: "anonymous", registered: false, queries: 12, blocked: 1, last_seen: 1_700_000_000 },
];

export const recent = (over: Partial<RecentQueries> = {}): RecentQueries => ({
  capacity: 600,
  queries: [
    {
      at: 1_700_000_000,
      device: "laptop",
      name: "example.com",
      type: "A",
      rcode: "NOERROR",
      cached: false,
      blocked: false,
      upstream: "alpha",
      latency_ms: 21,
    },
    {
      at: 1_700_000_001,
      device: "laptop",
      name: "stale.example",
      type: "A",
      rcode: "NOERROR",
      cached: true,
      blocked: false,
      upstream: "stale (upstream)",
      latency_ms: 1,
    },
    {
      at: 1_700_000_002,
      device: "tv",
      name: "budget.example",
      type: "A",
      rcode: "NOERROR",
      cached: false,
      blocked: false,
      upstream: "device budget",
      latency_ms: 0,
    },
    {
      at: 1_700_000_003,
      device: "tv",
      name: "stalebudget.example",
      type: "A",
      rcode: "NOERROR",
      cached: true,
      blocked: false,
      upstream: "stale (budget)",
      latency_ms: 1,
    },
    {
      at: 1_700_000_004,
      device: "tv",
      name: "ads.tracker.example",
      type: "A",
      rcode: "NOERROR",
      cached: false,
      blocked: true,
      upstream: "blocklist hagezi-pro → 0.0.0.0",
      latency_ms: 0,
    },
  ],
  devices: logDevices,
  hidden_domains: ["api.dns.example"],
  ...over,
});

export const history = (over: Partial<HistoryResponse> = {}): HistoryResponse => ({
  rows: [
    {
      id: 1,
      at: "2024-01-01T10:00:00Z",
      device: "laptop",
      name: "stored.example",
      qtype: "A",
      rcode: "NOERROR",
      cached: false,
      blocked: false,
      upstream: "alpha",
      latency_ms: 30,
    },
  ],
  next_before: null,
  available: true,
  hidden_domains: [],
  ...over,
});

export const accounts = (over: Partial<AccountsResponse> = {}): AccountsResponse => ({
  accounts: [
    {
      label: "alpha",
      username: "alpha@example.com",
      enabled: true,
      auth: {
        status: "ok",
        authenticated: true,
        last_error: "",
        last_success_at: 1_700_000_000,
        consecutive_failures: 0,
      },
      device_id: "dev-1",
      device_name: "Hydra DNS",
      dns_server_id: "srv-1",
      upstream_configured: true,
      requests_used: 30_000,
      requests_limit: 300_000,
      limits_fetched_at: 1_700_000_000,
      limits: { requests: { used: 30_000, limit: 300_000 }, devices: { used: 1, limit: 5 } },
      last_error: "",
      upstream: upstream("alpha", { share: 1 }),
    },
  ],
  store: {
    configured: true,
    editable: true,
    count: 1,
    enabled_count: 1,
    from_file: true,
    min_accounts: 1,
    min_enabled: 1,
    last_error: "",
  },
  ...over,
});

export const devices = (over: Partial<DevicesResponse> = {}): DevicesResponse => ({
  devices: [
    {
      id: "laptop",
      name: "Work Laptop",
      tags: ["work"],
      source: "store",
      token: "abcdefgh12345678",
      dot_hostname: "abcdefgh12345678.dot.example",
      doh_url: "https://api.dns.example/dns-query/abcdefgh12345678",
      json_url: "https://api.dns.example/dns-query/abcdefgh12345678?name=",
      mobileconfig_url: "https://api.dns.example/profile/abcdefgh12345678",
      setup_url: "https://api.dns.example/setup/abcdefgh12345678",
      clients: {
        firefox: { path: "Settings, DNS over HTTPS", value: "https://api.dns.example/x" },
      },
      rules: ["tracking.example"],
      safe_search: true,
      safe_search_skip: [],
      block_mode: "zero",
      block_address: "",
      hourly_budget: 0,
      safe_search_covers: ["Google", "YouTube"],
      paused_until: null,
      quiet_since: null,
      activity: {
        device: "laptop",
        queries: 4200,
        blocked: 900,
        cached: 1800,
        errors: 0,
        last_seen: 1_700_000_000,
        month: { queries: 310, blocked: 40, cached: 120, errors: 0 },
        top_domains: [],
      },
    },
  ],
  anonymous_allowed: false,
  base_url: "https://api.dns.example",
  store: {
    configured: true,
    writable: true,
    count: 1,
    last_sync_at: 1_700_000_000,
    last_error: "",
    using_seed: false,
  },
  tags: ["work"],
  ...over,
});

export const rewrites = (over: Partial<RewritesResponse> = {}): RewritesResponse => ({
  rewrites: [{ name: "nas.home", address: "192.168.1.5" }],
  editable: true,
  ...over,
});

export const nativeDns = () => ({
  accounts: [
    {
      account: "alpha",
      device_id: "dev-1",
      private_dns_hostname: "alpha.dns.adguard.com",
      dot_url: null,
      doq_url: null,
      doh_url: null,
      adguard_app_link: null,
      ip_addresses: [],
      requests_used: 30_000,
      requests_limit: 300_000,
      requests_remaining: 270_000,
      available: true,
    },
  ],
  recommended: "alpha",
});

export const policy = (over: Partial<PolicyResponse> = {}): PolicyResponse => ({
  accounts: ["alpha", "beta"],
  rules_text: "||doubleclick.net^\n||ads.example^",
  rules_max: 100,
  errors: {},
  policy: {
    rules: {
      rules: [
        { rule: "||doubleclick.net^", accounts: ["alpha", "beta"], everywhere: true },
        { rule: "||ads.example^", accounts: ["alpha"], everywhere: false },
      ],
      in_sync: false,
    },
    protection: { value: true, in_sync: true, per_account: { alpha: true, beta: true } },
    filter_lists: { value: null, in_sync: false, per_account: { alpha: ["1"], beta: ["1", "2"] } },
    filter_lists_enabled: { value: true, in_sync: true, per_account: { alpha: true, beta: true } },
    rules_enabled: { value: true, in_sync: true, per_account: { alpha: true, beta: true } },
    safe_browsing: { value: true, in_sync: true, per_account: { alpha: true, beta: true } },
    parental_control: {
      value: false,
      in_sync: true,
      per_account: { alpha: false, beta: false },
    },
    unprotected_accounts: [],
    drifting: ["user rules", "filter lists"],
    outliers: {},
    split: ["user rules", "filter lists"],
    in_sync: false,
    filter_list_names: { "1": "AdGuard DNS filter", "2": "Phishing URL Blocklist" },
  },
  ...over,
});

export const driftingPolicy = (): PolicyResponse => {
  const base = policy();
  const four = { alpha: true, bravo: true, charlie: true, echo: true };
  const same = { value: true, in_sync: true, per_account: four };
  return {
    ...base,
    accounts: ["alpha", "bravo", "charlie", "echo"],
    rules_text: "||doubleclick.net^",
    policy: {
      ...base.policy!,
      rules: {
        rules: [
          { rule: "||doubleclick.net^", accounts: ["alpha", "bravo", "charlie"], everywhere: false },
        ],
        in_sync: false,
      },
      protection: same,
      filter_lists: {
        value: ["1"],
        in_sync: true,
        per_account: { alpha: ["1"], bravo: ["1"], charlie: ["1"], echo: ["1"] },
      },
      filter_lists_enabled: { value: null, in_sync: false, per_account: { ...four, echo: false } },
      rules_enabled: same,
      safe_browsing: same,
      parental_control: {
        value: false,
        in_sync: true,
        per_account: { alpha: false, bravo: false, charlie: false, echo: false },
      },
      drifting: ["user rules", "filter lists switch"],
      outliers: { echo: ["user rules", "filter lists switch"] },
      split: [],
      in_sync: false,
    },
  };
};

export const lookupResult = (over: Partial<LookupResult> = {}): LookupResult => ({
  answer: {
    StatusName: "NOERROR",
    Answer: [{ name: "example.com", type_name: "A", TTL: 300, data: "93.184.216.34" }],
    Authority: [],
  },
  cached: false,
  blocked: false,
  upstream: "alpha",
  rcode: "NOERROR",
  ttl: 300,
  elapsed_ms: 24,
  ...over,
});

export const topRows = () => ({
  rows: [
    { domain: "doubleclick.net", queries: 310, blocked: 310 },
    { domain: "example.com", queries: 128, blocked: 0 },
  ],
  errors: {},
});

export const tokenStore = (over: Partial<TokenStoreStatus> = {}): TokenStoreStatus => ({
  configured: true,
  writable: true,
  count: 0,
  last_sync_at: 1_700_000_000,
  last_error: "",
  max: 25,
  scopes: ["read", "metrics"],
  ...over,
});

export const alertStatus = (over: Partial<AlertStatus> = {}): AlertStatus => ({
  configured: false,
  active: 0,
  reasons: [],
  sent: 0,
  failures: 0,
  last_sent_at: null,
  last_error: "",
  every_s: 600,
  repeat_s: 21_600,
  ...over,
});

export const digestStatus = (over: Partial<DigestStatus> = {}): DigestStatus => ({
  configured: false,
  enabled: false,
  day: "Mon",
  hour: 9,
  due_at: 1_700_003_600,
  covering_since: 1_699_900_000,
  sent: 0,
  failures: 0,
  last_sent_at: null,
  last_error: "",
  ...over,
});

export const digestDevices = (
  over: Partial<DigestDevicesResponse> = {},
): DigestDevicesResponse => ({
  available: true,
  since: Date.now() / 1000 - 3 * 86_400,
  until: Date.now() / 1000,
  span: "3 days",
  limit: 10,
  active: 3,
  watching: true,
  devices: [
    {
      device: "phone",
      name: "Pixel",
      queries: 820,
      blocked: 41,
      first_seen: {
        count: 2,
        names: [
          { name: "shop.example", at: Date.now() / 1000 - 3600 },
          { name: "news.example", at: Date.now() / 1000 - 7200 },
        ],
      },
      blocked_names: [
        { name: "ads.example", count: 30, layer: "blocklist", label: "blocklist Ads", blocked_now: true },
        { name: "tracker.example", count: 8, layer: "adguard", label: "AdGuard", blocked_now: true },
        {
          name: "late.example",
          count: 3,
          layer: "schedule",
          label: "schedule Bedtime",
          blocked_now: true,
        },
        {
          name: "nas.example",
          count: 2,
          layer: "rebind guard",
          label: "rebind guard",
          blocked_now: true,
        },
        {
          name: "metrics.shop.example",
          count: 2,
          layer: "cname",
          label: "cname collect.tracker.net · blocklist Ads",
          blocked_now: true,
        },
      ],
    },
    {
      device: "laptop",
      name: "Work Laptop",
      queries: 120,
      blocked: 0,
      first_seen: { count: 0, names: [] },
      blocked_names: [],
    },
  ],
  ...over,
});

export const backup = (over: Record<string, unknown> = {}) => ({
  version: 1,
  generated_at: 1_700_000_000,
  service: "Hydra DNS",
  devices: [{ id: "laptop", name: "Work Laptop" }],
  rewrites: {},
  accounts: [{ label: "alpha", username: "a@example.test", password: "secret" }],
  accounts_included: true,
  ...over,
});

export const auditResponse = (over: Record<string, unknown> = {}) => ({
  entries: [
    {
      at: 1_700_000_000,
      actor: "dashboard",
      address: "203.0.113.10",
      action: "device.remove",
      target: "laptop",
      outcome: "ok" as const,
      status: 200,
      detail: {},
      request_id: "abc123",
    },
  ],
  actions: ["device.remove"],
  actors: ["dashboard"],
  available: true,
  next_before: null,
  source: "live" as const,
  store: {
    persisted: false,
    in_memory: 1,
    pending: 0,
    writes: 0,
    last_flush_at: null,
    last_error: "",
    retention_days: 365,
  },
  ...over,
});

export const ruleGroups = (over: Partial<RuleGroupsResponse> = {}): RuleGroupsResponse => ({
  groups: [
    {
      id: "bedtime",
      name: "Bedtime",
      rules: ["tiktok.com", "instagram.com"],
      tags: ["kids"],
      days: [0, 1, 2, 3, 4],
      starts_at: "22:00",
      ends_at: "07:00",
      enabled: true,
      when: "22:00–07:00, Mon Tue Wed Thu Fri",
    },
    {
      id: "always",
      name: "Trackers",
      rules: ["doubleclick.net"],
      tags: [],
      days: [],
      starts_at: "",
      ends_at: "",
      enabled: true,
      when: "every day",
    },
  ],
  active: ["always"],
  timezone: "Europe/Dublin",
  editable: true,
  tags: ["kids", "work"],
  ...over,
});

const hoursAgo = (hours: number) => Math.floor(Date.now() / 1000) - hours * 3600;

export const blocklists = (over: Partial<BlocklistsResponse> = {}): BlocklistsResponse => ({
  lists: [
    {
      id: "hagezi-pro",
      name: "hagezi-pro",
      url: "https://cdn.jsdelivr.net/gh/hagezi/dns-blocklists@latest/domains/pro.txt",
      tags: ["kids", "tv"],
      allow: ["cdn.example.com"],
      enabled: true,
      created_at: "2026-09-01T10:00:00Z",
      names: 124_301,
      skipped: 12,
      hits: 842,
      fetched_at: hoursAgo(3),
      last_error: "",
      loaded: true,
      reaches: ["tv"],
      overlap: [],
      unique: null,
    },
    {
      id: "trackers",
      name: "Trackers",
      url: "https://example.org/trackers.txt",
      tags: [],
      allow: [],
      enabled: true,
      created_at: "2026-09-02T10:00:00Z",
      names: 5_120,
      skipped: 0,
      hits: 31,
      fetched_at: hoursAgo(20),
      last_error: "",
      loaded: true,
      reaches: ["laptop", "pixel", "tv"],
      overlap: [],
      unique: null,
    },
    {
      id: "broken",
      name: "Broken",
      url: "https://lists.example.net/missing.txt",
      tags: ["work"],
      allow: [],
      enabled: true,
      created_at: "2026-09-03T10:00:00Z",
      names: 0,
      skipped: 0,
      hits: 0,
      fetched_at: null,
      last_error: "HTTP 404 from lists.example.net",
      loaded: false,
      reaches: ["laptop"],
      overlap: [],
      unique: null,
    },
  ],
  editable: true,
  tags: ["kids", "tv", "work"],
  max_lists: 8,
  max_names: 400_000,
  total_names: 129_421,
  refresh_seconds: 86_400,
  ...over,
});

const HAGEZI = "https://cdn.jsdelivr.net/gh/hagezi/dns-blocklists@latest/wildcard";

export const blocklistPresets = (
  over: Partial<BlocklistPresetsResponse> = {},
): BlocklistPresetsResponse => ({
  presets: [
    {
      id: "hagezi-pro",
      name: "HaGeZi Pro",
      url: `${HAGEZI}/pro-onlydomains.txt`,
      format: "domains",
      names: 228_217,
      bytes: 4_365_473,
      subscribed: false,
      fits: true,
      unavailable: "",
    },
    {
      id: "hagezi-tif",
      name: "HaGeZi TIF",
      url: `${HAGEZI}/tif-onlydomains.txt`,
      format: "domains",
      names: 2_291_474,
      bytes: 39_838_576,
      subscribed: false,
      fits: false,
      unavailable: "38 MiB, over HYDRA_BLOCKLIST_MAX_BYTES",
    },
    {
      id: "oisd-small",
      name: "OISD small",
      url: "https://small.oisd.nl/domainswild2",
      format: "domains",
      names: 56_387,
      bytes: 1_096_593,
      subscribed: true,
      fits: true,
      unavailable: "already added",
    },
  ],
  headroom: 270_579,
  max_bytes: 20 * 1024 * 1024,
  ...over,
});

export const domainResponse =(over: Partial<DomainResponse> = {}): DomainResponse => ({
  name: "example.com",
  devices: [
    {
      id: "laptop",
      name: "Work Laptop",
      tags: ["work"],
      decision: null,
      blocked: false,
    },
    {
      id: "pixel",
      name: "Pixel 9",
      tags: [],
      decision: {
        layer: "allowed",
        label: "allowed by @@ device",
        blocked: false,
        source: null,
        rule: "example.com",
      },
      blocked: false,
    },
    {
      id: "tv",
      name: "Living room TV",
      tags: ["kids"],
      decision: {
        layer: "blocklist",
        label: "blocklist hagezi-pro",
        blocked: true,
        source: "hagezi-pro",
      },
      blocked: true,
    },
  ],
  askers: [
    {
      device: "laptop",
      name: "Work Laptop",
      count: 412,
      last_at: 1_700_000_000,
      last_upstream: "alpha",
      last_result: "allowed",
      first_at: 1_699_000_000,
    },
    {
      device: "tv",
      name: "Living room TV",
      count: 18,
      last_at: 1_700_000_100,
      last_upstream: "blocklist hagezi-pro → 0.0.0.0",
      last_result: "blocked",
      first_at: null,
    },
  ],
  cache: { A: { state: "live", ttl: 120 }, AAAA: { state: "absent", ttl: 0 } },
  lists: [{ id: "hagezi-pro", name: "hagezi-pro", allowed: false, enabled: true }],
  suspicion: { score: 0, reasons: [], threshold: 50 },
  editable: true,
  ...over,
});

export const firstSeenResponse = (over: Partial<FirstSeenResponse> = {}): FirstSeenResponse => ({
  names: [
    {
      at: 1_700_000_200,
      device: "laptop",
      name: "xjw4k2qzp9v.com",
      score: 80,
      reasons: ["entropy", "digits", "consonants"],
      suspicious: true,
    },
    {
      at: 1_700_000_100,
      device: "pixel",
      name: "fonts.gstatic.com",
      score: 0,
      reasons: [],
      suspicious: false,
    },
  ],
  watching: true,
  tracking: {
    counted: 2,
    suspicious: 1,
    threshold: 50,
    devices: 2,
    tracked: 40,
    capacity: 40_000,
    warmup_s: 3600,
    recent: 2,
  },
  devices: logDevices,
  ...over,
});

export const deviceRuleEntryResult = (id = "laptop", rules: string[] = ["example.com"]) => ({
  id,
  rules,
});

export const groupRuleEntryResult = (id = "bedtime", domain = "example.com") => {
  const group = ruleGroups().groups.find((entry) => entry.id === id) ?? ruleGroups().groups[0];
  return { ...group, rules: [...group.rules, domain] };
};

export const blocklistAllowResult = (id = "hagezi-pro", allow: string[] = ["example.com"]) => ({
  id,
  allow,
});

const ICON =
  '<svg xmlns="http://www.w3.org/2000/svg" fill="currentColor" viewBox="0 0 1 1"><path d="M0 0h1v1H0z"/></svg>';

export const appCatalog = (over: Partial<AppCatalog> = {}): AppCatalog => ({
  fingerprint: "abc123",
  source: "bundled",
  fetched_at: null,
  groups: ["streaming", "social_network", "gaming"],
  apps: [
    { id: "discord", name: "Discord", group: "social_network", icon: ICON, domains: 12 },
    { id: "roblox", name: "Roblox", group: "gaming", icon: ICON, domains: 8 },
    { id: "tiktok", name: "TikTok", group: "social_network", icon: ICON, domains: 30 },
    { id: "youtube", name: "YouTube", group: "streaming", icon: ICON, domains: 14 },
    ...Array.from({ length: 30 }, (_, index) => ({
      id: `game${index}`,
      name: `Game ${String(index).padStart(2, "0")}`,
      group: "gaming",
      icon: "",
      domains: 1,
    })),
  ],
  ...over,
});

export const apps = (over: Partial<AppsResponse> = {}): AppsResponse => ({
  blocks: [
    {
      id: "youtube",
      name: "YouTube",
      group: "streaming",
      known: true,
      tags: ["kids"],
      days: [],
      starts_at: "21:00",
      ends_at: "07:00",
      enabled: true,
      created_at: "2026-09-30T10:00:00Z",
      when: "21:00–07:00, every day",
      active_now: true,
      hits: 1204,
      reaches: ["tv"],
      seen: [{ id: "tv", name: "Living room TV", at: Date.now() / 1000 - 120 }],
    },
    {
      id: "tiktok",
      name: "TikTok",
      group: "social_network",
      known: true,
      tags: [],
      days: [],
      starts_at: "",
      ends_at: "",
      enabled: false,
      created_at: "2026-09-30T11:00:00Z",
      when: "always",
      active_now: false,
      hits: 0,
      reaches: ["tv", "laptop"],
      seen: [],
    },
  ],
  seen: [
    { id: "roblox", devices: 2, last_at: Date.now() / 1000 - 300 },
    { id: "youtube", devices: 1, last_at: Date.now() / 1000 - 120 },
  ],
  editable: true,
  tags: ["kids", "work"],
  timezone: "Asia/Kolkata",
  catalog: { fingerprint: "abc123", source: "bundled", apps: 34, fetched_at: null, error: "" },
  ...over,
});
