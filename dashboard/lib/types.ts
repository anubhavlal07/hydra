export interface QuotaRollup {
  accounts_reporting: number;
  accounts_disabled: number;
  requests_limit: number;
  requests_used: number;
  requests_remaining: number;
  used_fraction: number;
  cache_hit_rate: number;
  cache_multiplier: number;
  effective_remaining: number;
  effective_limit: number;
  headroom_days: number | null;
}

export interface ResolverSummary {
  queries: number;
  cached: number;
  blocked: number;
  errors: number;
  refused: number;
  cache_hit_rate: number;
  block_rate: number;
  uptime_s: number;
  latency: { p50: number; p95: number; p99: number };
  month?: MonthCounts & { label: string; since: number };
}

export interface MonthCounts {
  queries: number;
  blocked: number;
  cached: number;
  errors: number;
}

export interface CacheSnapshot {
  entries: number;
  capacity: number;
  hits: number;
  misses: number;
  hit_rate: number;
  evictions: number;
  expired: number;

  live?: number;
  stale?: number;
  stale_hits?: number;
  restored?: number;
  stale_window_s?: number;
}

export interface UpstreamSnapshot {
  label: string;
  configured: boolean;
  enabled: boolean;
  available: boolean;
  benched: boolean;
  benched_for_s: number;
  exhausted: boolean;
  queries: number;
  errors: number;
  error_rate: number;
  latency_ms: number;
  quota_used: number;
  quota_limit: number;
  quota_remaining: number;
  quota_fraction: number;
  share: number;
  deficit: number;
  last_error: string;
  last_used_at: number | null;
}

export interface BalanceReport {
  strategy: string;
  balancing: boolean;
  floor: number;
  reporting: number;
  target_fraction: number;
  spread: number;
  balanced: boolean;
  shares: Record<string, number>;
}

export interface Overview {
  service: string;
  resolver_url: string;
  strategy: string;
  warnings: string[];
  quota: QuotaRollup;
  resolver: ResolverSummary;
  cache: CacheSnapshot;
  accounts: {
    total: number;
    enabled: number;
    authenticated: number;
    serving: number;
    discovery_done: boolean;
    last_refresh_at: number | null;
  };
  upstreams: UpstreamSnapshot[];
  pool: PoolState;
  attention: string[];
  certificate: CertificateStatus | null;
  prefetch: PrefetchStatus | null;
  devices: number;
  saved?: QuotaSaved | null;
}

export type SavedCause = "app" | "blocklist" | "rule" | "cache" | "coalesced";

export interface QuotaSaved {
  month: string;
  since: number;
  until: number;
  by_cause: Partial<Record<SavedCause, number>>;
  gross: number;
  prefetch_cost: number;
  net: number;
  allowance: number | null;
  fraction: number | null;
  partial: boolean;
}

export interface PoolState {
  ready: boolean;
  spent: boolean;
  configured: number;
  available: number;
  balance: BalanceReport;
  fallback: {
    active: boolean;
    url: string;
    queries: number;
    errors: number;
    latency_ms: number;
    last_error: string;
  } | null;
}

export interface AccountRow {
  label: string;
  username: string;
  enabled: boolean;
  auth: {
    status: "ok" | "error" | "pending";
    authenticated: boolean;
    last_error: string;
    last_success_at: number | null;
    consecutive_failures: number;
  };
  device_id: string | null;
  device_name: string | null;
  dns_server_id: string | null;
  upstream_configured: boolean;
  requests_used: number;
  requests_limit: number;
  limits_fetched_at: number | null;
  limits: Record<string, { used: number; limit: number }>;
  last_error: string;
  upstream: UpstreamSnapshot | null;
}

export interface TimelinePoint {
  at: number;
  queries: number;
  blocked: number;
  cached: number;
}

export interface DeviceActivity {
  device: string;
  queries: number;
  blocked: number;
  cached: number;
  errors: number;
  last_seen: number | null;
  month?: MonthCounts;
  top_domains: { domain: string; queries: number }[];
}

export interface ResolverDetail {
  summary: ResolverSummary;
  timeline: TimelinePoint[];
  devices: DeviceActivity[];
  top_domains: { domain: string; queries: number; blocked: number }[];
  top_blocked: { domain: string; queries: number; blocked: number }[];
  qtypes: { type: string; queries: number }[];
  upstreams: UpstreamSnapshot[];
  cache: CacheSnapshot;
}

export interface LiveQuery {
  at: number;
  device: string;
  name: string;
  type: string;
  rcode: string;
  cached: boolean;
  blocked: boolean;
  upstream: string;
  latency_ms: number;
}

export interface CertificateStatus {
  known: boolean;
  expires_at: number | null;
  days_left: number | null;
}

export interface PrefetchStatus {
  enabled: boolean;
  renewed: number;
  failed: number;
  min_hits: number;
  window_s: number;

  warmed?: number;
}

export interface ListenerSnapshot {
  enabled: boolean;
  error?: string;
  base_domain?: string;
  port?: number;
  connections?: number;
  open_connections?: number;
  queries?: number;
  refused_unknown_sni?: number;
  refused_no_sni?: number;
  malformed?: number;
  handshake_failures?: number;
  uptime_s?: number;
}

export interface CertificateWatch {
  watching: boolean;
  listeners: string[];
  poll_s: number;
  reloads: number;
  failures: number;
  last_reload_at: number | null;
  last_error: string;
}

export interface CachePersistence {
  enabled: boolean;
  path: string;
  restored: number;
  saved: number;
  last_save_at: number | null;
  last_error: string;
}

export interface DeviceBudget {
  device: string;
  hourly: number;
  remaining: number;
  used: number;
  denied: number;
  breached_at: number | null;
}

export interface BudgetStatus {
  enabled: boolean;
  hourly: number;
  denied: number;
  devices: DeviceBudget[];
}

export interface CoalescingStatus {
  shared: number;
  led: number;
  in_flight: number;
}

export interface RebindStatus {
  enabled: boolean;
  allow: string[];
  refused: number;
  failed: number;
}

export interface UncloakStatus {
  enabled: boolean;
  blocked: number;
  failed: number;
}

export interface BlocklistHealth {
  lists: number;
  enabled: number;
  loaded: number;
  failing: number;
  names: number;
  hits: number;
  max_lists: number;
  max_names: number;
  refresh_seconds: number;
  on_disk: boolean;
  last_sync_at: number | null;
  last_error: string;
}

export interface Pulse {
  overview: Overview;
  resolver: ResolverDetail;
  health: Health;
}

export interface Health {
  status: string;
  uptime_s: number;

  upstreams_ready?: number;
  accounts?: number;
  discovery_done?: boolean;
  attention?: string[];
  devices?: number;
  warnings?: string[];
  dot?: ListenerSnapshot | null;
  doq?: ListenerSnapshot | null;

  certificate_watch?: CertificateWatch | null;
  cache_snapshot?: CachePersistence | null;
  budget?: BudgetStatus | null;
  coalescing?: CoalescingStatus | null;
  blocklists?: BlocklistHealth | null;
  rebind?: RebindStatus | null;
  cname?: UncloakStatus | null;
  instance?: string;
  audit?: AuditStore | null;
  alerts?: AlertStatus | null;
  tokens?: TokenStoreStatus | null;
  quiet?: QuietWatch | null;
}

export interface QuietWatch {
  quiet_s: number;
  established_hours: number;
  tracked: number;
  established: number;
  settled: boolean;
  settles_at: number;
}

export interface FirstSeenName {
  at: number;
  device: string;
  name: string;
  score?: number;
  reasons?: string[];
  suspicious?: boolean;
}

export interface Suspicion {
  score: number;
  reasons: string[];
  threshold: number;
}

export interface FirstSeenResponse {
  names: FirstSeenName[];
  watching: boolean;
  tracking: {
    counted: number;
    devices: number;
    tracked: number;
    capacity: number;
    warmup_s: number;
    recent: number;
    suspicious?: number;
    threshold?: number;
  };
  devices: LogDevice[];
}

export interface HistoryRow {
  id: number;
  at: string;
  device: string;
  name: string;
  qtype: string | null;
  rcode: string | null;
  cached: boolean;
  blocked: boolean;
  upstream: string | null;
  latency_ms: number | null;
}

export interface HistoryResponse {
  rows: HistoryRow[];
  next_before: number | null;
  available: boolean;
  hidden_domains?: string[];
}

export interface Rewrite {
  name: string;
  address: string;
}

export interface RewritesResponse {
  rewrites: Rewrite[];
  editable: boolean;
}

export interface LogDevice {
  id: string;
  name: string;
  registered: boolean;
  queries: number;
  blocked: number;
  last_seen: number | null;
  month_queries?: number;
}

export interface RecentQueries {
  queries: LiveQuery[];
  devices: LogDevice[];
  hidden_domains: string[];
  capacity: number;
}

export interface AdGuardLogItem {
  account: string;
  time_millis: number;
  domain: string;
  dns_request_type?: string;
  company_id?: string;
  client_country?: string;
  category_type?: string;
  device_id?: string;
  dnssec?: boolean;
  filtering_info?: { filtering_status?: string; filter_lists?: string[] };
}

export interface StatsTimeline {
  window: string;
  series: { at: number; queries: number; blocked: number }[];
  totals: { queries: number; blocked: number };
  per_account: Record<string, number>;
  errors: Record<string, string>;
}

export interface TopRow {
  domain?: string;
  company?: string;
  country?: string;
  category?: string;
  queries: number;
  blocked: number;
}

export interface StoreStatus {
  configured: boolean;
  writable: boolean;
  count: number;
  last_sync_at: number | null;
  last_error: string;
  using_seed: boolean;
}

export interface NativeDnsAccount {
  account: string;
  device_id: string | null;

  private_dns_hostname: string | null;
  dot_url: string | null;
  doq_url: string | null;
  doh_url: string | null;
  adguard_app_link: string | null;
  ip_addresses: string[];
  requests_used: number;
  requests_limit: number;
  requests_remaining: number;
  available: boolean;
}

export interface DeviceConfig {
  id: string;
  name: string;
  tags: string[];

  source: "store" | "env";

  token: string;

  dot_hostname: string;
  doh_url: string;
  json_url: string;
  mobileconfig_url: string;
  setup_url?: string;
  clients: Record<string, { path: string; value: string }>;
  rules: string[];
  safe_search: boolean;
  safe_search_skip: string[];
  block_mode: "zero" | "nxdomain" | "address";
  hourly_budget: number;
  block_address: string;
  safe_search_covers: string[];
  paused_until: number | null;
  activity: DeviceActivity | null;
  quiet_since?: number | null;
}

export interface DevicesResponse {
  devices: DeviceConfig[];
  anonymous_allowed: boolean;
  base_url: string;
  store: StoreStatus;
  tags: string[];
}

export interface LookupResult {
  answer: {
    StatusName: string;
    Answer: { name: string; type_name: string; TTL: number; data: string }[];
    Authority: { name: string; type_name: string; TTL: number; data: string }[];
  };
  cached: boolean;
  blocked: boolean;
  upstream: string;
  rcode: string;
  ttl: number | null;
  elapsed_ms: number;
}

export interface RuleRow {
  rule: string;

  accounts: string[];
  everywhere: boolean;
}

export interface SettingDiff<T> {
  value: T | null;
  in_sync: boolean;
  per_account: Record<string, T>;
}

export interface PolicySummary {
  rules: { rules: RuleRow[]; in_sync: boolean };
  protection: SettingDiff<boolean>;
  filter_lists: SettingDiff<string[]>;
  filter_lists_enabled: SettingDiff<boolean>;
  rules_enabled: SettingDiff<boolean>;
  safe_browsing: SettingDiff<boolean>;
  parental_control: SettingDiff<boolean>;
  youtube_safe_search?: SettingDiff<boolean>;
  engines_safe_search?: SettingDiff<boolean>;

  unprotected_accounts: string[];
  drifting: string[];
  outliers: Record<string, string[]>;
  split: string[];
  in_sync: boolean;
  filter_list_names: Record<string, string>;
}

export interface PolicySyncResult extends PolicyWriteResult {
  repaired: Record<string, string[]>;
  left: string[];
  read_errors: Record<string, string>;
}

export interface PolicyAdoption {
  synced: boolean;
  applied: string[];
  left: string[];
  from: string[];
  error: string;
}

export interface PolicyResponse {
  accounts: string[];
  policy: PolicySummary | null;
  rules_text: string;
  rules_max: number;
  errors: Record<string, string>;
}

export interface PolicyWriteResult {
  applied_to: string[];
  errors: Record<string, string>;
  cache_cleared: number;
  in_sync: boolean;
}

export interface AccountStoreStatus {
  configured: boolean;
  editable: boolean;
  count: number;
  enabled_count: number;
  from_file: boolean;
  min_accounts: number;
  min_enabled: number;
  last_error: string;
}

export interface AccountsResponse {
  accounts: AccountRow[];
  store: AccountStoreStatus;
}

export interface AuditEntry {
  id?: number;
  at: number;
  actor: string;
  address: string;
  action: string;
  target: string;
  outcome: "ok" | "denied" | "failed";
  status: number;
  detail: Record<string, unknown>;
  request_id: string;
}

export interface AuditStore {
  persisted: boolean;
  in_memory: number;
  pending: number;
  writes: number;
  last_flush_at: number | null;
  last_error: string;
  retention_days: number;
}

export interface AuditResponse {
  entries: AuditEntry[];
  actions?: string[];
  actors?: string[];
  available: boolean;
  next_before: number | null;
  source: "live" | "history";
  store?: AuditStore;
}

export interface MachineToken {
  id: string;
  name: string;
  scope: "read" | "metrics";
  created_at: number | null;
  last_used_at: number | null;
}

export interface TokenStoreStatus {
  configured: boolean;
  writable: boolean;
  count: number;
  last_sync_at: number | null;
  last_error: string;
  max: number;
  scopes: string[];
}

export interface TokensResponse {
  tokens: MachineToken[];
  store: TokenStoreStatus;
  scopes?: string[];
}

export interface AlertStatus {
  configured: boolean;
  active: number;
  reasons: string[];
  sent: number;
  failures: number;
  last_sent_at: number | null;
  last_error: string;
  every_s: number;
  repeat_s: number;
}

export interface DigestStatus {
  configured: boolean;
  enabled: boolean;
  day: string;
  hour: number;
  due_at: number;
  covering_since: number;
  sent: number;
  failures: number;
  last_sent_at: number | null;
  last_error: string;
}

export type DigestLayer =
  | "app"
  | "blocklist"
  | "device rule"
  | "schedule"
  | "safe search"
  | "paused"
  | "allowed"
  | "rewrite"
  | "designated"
  | "local ptr"
  | "local zone"
  | "rebind guard"
  | "cname"
  | "adguard";

export interface DigestBlockedName {
  name: string;
  count: number;
  layer: DigestLayer;
  label: string;
  blocked_now: boolean;
}

export interface DigestDevice {
  device: string;
  name: string;
  queries: number;
  blocked: number;
  first_seen: { count: number; names: { name: string; at: number }[] };
  blocked_names: DigestBlockedName[];
}

export interface DigestDevicesResponse {
  available: boolean;
  since?: number;
  until?: number;
  span?: string;
  limit?: number;
  active?: number;
  watching?: boolean;
  devices: DigestDevice[];
}

export interface AlertsResponse {
  alerts: AlertStatus;
  webhook_set: boolean;
  digest: DigestStatus;
}

export interface RuleDiff {
  added: string[];
  removed: string[];
  unchanged: number;
  before: number;
  after: number;
  changed: boolean;
}

export interface SettingChange {
  field: string;
  from: unknown;
  to: unknown;
}

export interface PolicyPreview {
  rules: RuleDiff;
  settings: SettingChange[];
  errors: Record<string, string>;
}

export interface PolicyVersion {
  id: number;
  at: number;
  actor: string;
  note: string;
  rules: string[];
  settings: Record<string, unknown>;
  diff?: RuleDiff;
  settings_diff?: SettingChange[];
}

export interface PolicyVersionsResponse {
  versions: PolicyVersion[];
  available: boolean;
  current_rules?: number;
}

export interface BulkResult {
  applied: string[];
  failed: Record<string, string>;
  tags: string[];
}

export interface ImportResult {
  added: string[];
  updated: string[];
  skipped: Record<string, string>;
  dry_run: boolean;
  would_add?: string[];
  would_update?: string[];
  rows?: number;
}

export interface PushDevice {
  endpoint: string;
  label: string;
  created_at: number | null;
  last_ok_at: number | null;
  failures: number;
}

export interface PushResponse {
  configured: boolean;
  stored: boolean;
  devices: PushDevice[];
  public_key: string;
  sent: number;
  failures: number;
  pruned: number;
  last_error: string;
  max_devices: number;
}

export interface RuleGroup {
  id: string;
  name: string;
  rules: string[];
  tags: string[];
  days: number[];
  starts_at: string;
  ends_at: string;
  enabled: boolean;
  when: string;
}

export interface RuleGroupsResponse {
  groups: RuleGroup[];
  active: string[];
  timezone: string;
  editable: boolean;
  tags: string[];
}

export interface BlocklistOverlap {
  id: string;
  shared: number;
}

export interface Blocklist {
  id: string;
  name: string;
  url: string;
  tags: string[];
  allow: string[];
  enabled: boolean;
  created_at: string | null;
  names: number;
  skipped: number;
  hits: number;
  fetched_at: number | null;
  last_error: string;
  loaded: boolean;
  reaches: string[];
  overlap: BlocklistOverlap[];
  unique: number | null;
}

export interface BlocklistsResponse {
  lists: Blocklist[];
  editable: boolean;
  tags: string[];
  max_lists: number;
  max_names: number;
  total_names: number;
  refresh_seconds: number;
}

export interface BlocklistPreset {
  id: string;
  name: string;
  url: string;
  format: string;
  names: number;
  bytes: number;
  subscribed: boolean;
  fits: boolean;
  unavailable: string;
}

export interface BlocklistPresetsResponse {
  presets: BlocklistPreset[];
  headroom: number;
  max_bytes: number;
}

export type DecisionLayer =
  | "rewrite"
  | "designated"
  | "local ptr"
  | "local zone"
  | "paused"
  | "device rule"
  | "schedule"
  | "allowed"
  | "app"
  | "blocklist"
  | "safe search";

export interface Decision {
  layer: DecisionLayer;
  label: string;
  blocked: boolean;
  source: string | null;
  rule?: string | null;
  expires_at?: number;
}

export interface DomainDevice {
  id: string;
  name: string;
  tags: string[];
  decision: Decision | null;
  blocked: boolean;
}

export interface DomainAsker {
  device: string;
  name: string;
  count: number;
  last_at: number;
  last_upstream: string;
  last_result: "allowed" | "blocked" | "error";
  first_at: number | null;
}

export interface CachePeek {
  state: "live" | "stale" | "absent";
  ttl: number;
}

export interface DomainList {
  id: string;
  name: string;
  allowed: boolean;
  enabled: boolean;
}

export interface DomainResponse {
  name: string;
  devices: DomainDevice[];
  askers: DomainAsker[];
  cache: { A: CachePeek; AAAA: CachePeek };
  lists: DomainList[];
  apps?: DomainApp[];
  suspicion?: Suspicion;
  editable: boolean;
}

export interface AppSeenDevice {
  id: string;
  name: string;
  at: number;
}

export interface AppBlock {
  id: string;
  name: string;
  group: string;
  known: boolean;
  tags: string[];
  days: number[];
  starts_at: string;
  ends_at: string;
  enabled: boolean;
  created_at: string | null;
  when: string;
  active_now: boolean;
  hits: number;
  reaches: string[];
  seen: AppSeenDevice[];
}

export interface AppSeen {
  id: string;
  devices: number;
  last_at: number;
}

export interface AppsResponse {
  blocks: AppBlock[];
  seen: AppSeen[];
  editable: boolean;
  tags: string[];
  timezone: string;
  catalog: {
    fingerprint: string;
    source: string;
    apps: number;
    fetched_at: number | null;
    error: string;
  };
}

export interface CatalogApp {
  id: string;
  name: string;
  group: string;
  icon: string;
  domains: number;
}

export interface AppCatalog {
  fingerprint: string;
  source: string;
  fetched_at: number | null;
  groups: string[];
  apps: CatalogApp[];
}

export interface DomainApp {
  id: string;
  name: string;
  group: string;
  blocked: boolean;
  enabled: boolean;
  tags: string[];
  when: string;
}
