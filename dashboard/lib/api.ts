import type {
  AppBlock,
  AppCatalog,
  AppsResponse,
  AccountRow,
  AlertsResponse,
  AuditResponse,
  Blocklist,
  BlocklistPresetsResponse,
  BlocklistsResponse,
  BulkResult,
  DigestDevicesResponse,
  ImportResult,
  PolicyPreview,
  PolicyVersionsResponse,
  PushResponse,
  TokensResponse,
  AccountsResponse,
  AccountStoreStatus,
  AdGuardLogItem,
  DeviceConfig,
  DevicesResponse,
  DomainResponse,
  FirstSeenResponse,
  Health,
  HistoryResponse,
  NativeDnsAccount,
  LookupResult,
  Overview,
  PolicyResponse,
  Pulse,
  PolicyWriteResult,
  PolicyAdoption,
  PolicySyncResult,
  RecentQueries,
  ResolverDetail,
  RewritesResponse,
  RuleGroup,
  RuleGroupsResponse,
  StatsTimeline,
  TopRow,
} from "./types";

export const API_BASE = (
  process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8080"
).replace(/\/$/, "");

export class ApiError extends Error {
  readonly code: string;
  readonly status: number;

  constructor(message: string, code: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
  }

  get unauthorized(): boolean {
    return this.status === 401;
  }
}

let inFlight = 0;
const watchers = new Set<() => void>();

const validated = new Map<string, { etag: string; body: unknown }>();

export function forgetValidators(): void {
  validated.clear();
}

export function subscribeToRequests(listener: () => void): () => void {
  watchers.add(listener);
  return () => {
    watchers.delete(listener);
  };
}

export function requestsInFlight(): number {
  return inFlight;
}

function countRequest(delta: number) {
  inFlight = Math.max(0, inFlight + delta);
  for (const watcher of watchers) watcher();
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  countRequest(1);
  try {
    return await send<T>(path, init);
  } finally {
    countRequest(-1);
  }
}

async function send<T>(path: string, init: RequestInit = {}): Promise<T> {
  const method = (init.method ?? "GET").toUpperCase();
  const known = method === "GET" ? validated.get(path) : undefined;

  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...init,
      credentials: "include",
      headers: {
        "Content-Type": "application/json",
        ...(known ? { "If-None-Match": known.etag } : {}),
        ...(init.headers ?? {}),
      },
    });
  } catch {
    throw new ApiError(
      "Could not reach the resolver API. If this device uses Hydra for DNS, the resolver " +
        "being down also means this page cannot resolve its own API — check the service, or " +
        "switch to a secondary resolver and try again.",
      "unreachable",
      0,
    );
  }

  if (response.status === 304 && known) return known.body as T;
  if (response.status === 204) return undefined as T;

  let body: unknown = null;
  try {
    body = await response.json();
  } catch {}

  if (!response.ok) {
    validated.delete(path);
    const detail = (body as { detail?: { code?: string; message?: string } } | null)?.detail;
    throw new ApiError(
      detail?.message ?? `Request failed (${response.status})`,
      detail?.code ?? "error",
      response.status,
    );
  }

  const etag = response.headers.get("etag");
  if (method === "GET" && etag) validated.set(path, { etag, body });

  return body as T;
}

export const checkSession = () => request<{ authenticated: boolean }>("/api/session");

export const login = (password: string) =>
  request<{ ok: boolean }>("/api/login", {
    method: "POST",
    body: JSON.stringify({ password }),
  });

export const logout = () => {
  forgetValidators();
  return request<{ ok: boolean }>("/api/logout", { method: "POST" });
};

export const getOverview = () => request<Overview>("/api/overview");

export const getHealth = () => request<Health>("/api/health");

export const getAccounts = () => request<AccountsResponse>("/api/accounts");

export const getResolver = (minutes = 60) =>
  request<ResolverDetail>(`/api/resolver?minutes=${minutes}`);

export const getPulse = (minutes = 60) => request<Pulse>(`/api/pulse?minutes=${minutes}`);

export const getRecent = (limit = 150, device?: string, result?: string, q?: string) =>
  request<RecentQueries>(
    `/api/resolver/recent?limit=${limit}` +
      (device ? `&device=${encodeURIComponent(device)}` : "") +
      (result ? `&result=${encodeURIComponent(result)}` : "") +
      (q ? `&q=${encodeURIComponent(q)}` : ""),
  );

export const getHistory = (
  limit = 100,
  before?: number | null,
  device?: string,
  result?: string,
  q?: string,
) =>
  request<HistoryResponse>(
    `/api/resolver/history?limit=${limit}` +
      (before ? `&before=${before}` : "") +
      (device ? `&device=${encodeURIComponent(device)}` : "") +
      (result ? `&result=${encodeURIComponent(result)}` : "") +
      (q ? `&q=${encodeURIComponent(q)}` : ""),
  );

export const getFirstSeen = (limit = 50, device?: string, suspicious = false) =>
  request<FirstSeenResponse>(
    `/api/resolver/first-seen?limit=${limit}` +
      (device ? `&device=${encodeURIComponent(device)}` : "") +
      (suspicious ? "&suspicious=true" : ""),
  );

export const getStatsTimeline = (window: string) =>
  request<StatsTimeline>(`/api/stats/timeline?window=${window}`);

export const getTop = (kind: string, window: string, limit = 20) =>
  request<{ rows: TopRow[]; errors: Record<string, string> }>(
    `/api/stats/top?kind=${kind}&window=${window}&limit=${limit}`,
  );

export const getQueryLog = (window: string, cursor?: string, limit = 50, q?: string) =>
  request<{
    items: AdGuardLogItem[];
    next_cursor: string | null;
    errors: Record<string, string>;
    hidden_domains: string[];
  }>(
    `/api/query-log?window=${window}&limit=${limit}` +
      (cursor ? `&cursor=${encodeURIComponent(cursor)}` : "") +
      (q ? `&q=${encodeURIComponent(q)}` : ""),
  );

export const getDevices = () => request<DevicesResponse>("/api/devices");

export const getNativeDns = () =>
  request<{ accounts: NativeDnsAccount[]; recommended: string | null }>("/api/native-dns");

export const lookup = (name: string, type: string, dnssec = false) =>
  request<LookupResult>("/api/lookup", {
    method: "POST",
    body: JSON.stringify({ name, type, dnssec }),
  });

export const clearCache = () =>
  request<{ cleared: number }>("/api/cache/clear", { method: "POST" });

export const addDevice = (name: string, id?: string) =>
  request<DeviceConfig>("/api/devices", {
    method: "POST",
    body: JSON.stringify(id ? { name, id } : { name }),
  });

export const renameDevice = (deviceId: string, name: string) =>
  request<DeviceConfig>(`/api/devices/${encodeURIComponent(deviceId)}`, {
    method: "PATCH",
    body: JSON.stringify({ name }),
  });

export const deleteDevice = (deviceId: string) =>
  request<{ removed: boolean; id: string }>(
    `/api/devices/${encodeURIComponent(deviceId)}`,
    { method: "DELETE" },
  );

export const setDeviceRules = (deviceId: string, rules: string[]) =>
  request<DeviceConfig>(`/api/devices/${encodeURIComponent(deviceId)}/rules`, {
    method: "PUT",
    body: JSON.stringify({ rules }),
  });

export const setDevicePolicy = (
  deviceId: string,
  policy: {
    safe_search: boolean;
    block_mode: string;
    block_address: string;
    safe_search_skip?: string[];
  },
) =>
  request<DeviceConfig>(`/api/devices/${encodeURIComponent(deviceId)}/policy`, {
    method: "PUT",
    body: JSON.stringify(policy),
  });

export const pauseDevice = (deviceId: string, minutes: number) =>
  request<DeviceConfig>(`/api/devices/${encodeURIComponent(deviceId)}/pause`, {
    method: "POST",
    body: JSON.stringify({ minutes }),
  });

export const resumeDevice = (deviceId: string) =>
  request<DeviceConfig>(`/api/devices/${encodeURIComponent(deviceId)}/pause`, {
    method: "DELETE",
  });

export const setDeviceBudget = (deviceId: string, hourlyBudget: number) =>
  request<DeviceConfig>(`/api/devices/${encodeURIComponent(deviceId)}/budget`, {
    method: "PUT",
    body: JSON.stringify({ hourly_budget: hourlyBudget }),
  });

export const getRuleGroups = () => request<RuleGroupsResponse>("/api/rule-groups");

export const setRuleGroup = (
  groupId: string,
  body: {
    name: string;
    rules: string[];
    tags: string[];
    days: number[];
    starts_at: string;
    ends_at: string;
    enabled: boolean;
  },
) =>
  request<RuleGroup>(`/api/rule-groups/${encodeURIComponent(groupId)}`, {
    method: "PUT",
    body: JSON.stringify(body),
  });

export const removeRuleGroup = (groupId: string) =>
  request<{ removed: boolean; id: string }>(
    `/api/rule-groups/${encodeURIComponent(groupId)}`,
    { method: "DELETE" },
  );

export const getBlocklists = () => request<BlocklistsResponse>("/api/blocklists");

export const getBlocklistPresets = () =>
  request<BlocklistPresetsResponse>("/api/blocklists/presets");

export const setBlocklist = (
  listId: string,
  body: {
    name: string;
    url: string;
    tags: string[];
    allow: string[];
    enabled: boolean;
  },
) =>
  request<Blocklist>(`/api/blocklists/${encodeURIComponent(listId)}`, {
    method: "PUT",
    body: JSON.stringify(body),
  });

export const refreshBlocklist = (listId: string) =>
  request<Blocklist>(`/api/blocklists/${encodeURIComponent(listId)}/refresh`, {
    method: "POST",
  });

export const blocklistAllow = (listId: string, domain: string, action: "add" | "remove") =>
  request<Blocklist>(`/api/blocklists/${encodeURIComponent(listId)}/allow`, {
    method: "POST",
    body: JSON.stringify({ domain, action }),
  });

export const removeBlocklist = (listId: string) =>
  request<{ removed: boolean; id: string }>(`/api/blocklists/${encodeURIComponent(listId)}`, {
    method: "DELETE",
  });

export const getApps = () => request<AppsResponse>("/api/apps");

export const getAppCatalog = () => request<AppCatalog>("/api/apps/catalog");

export const setAppBlock = (
  appId: string,
  body: {
    tags: string[];
    days: number[];
    starts_at: string;
    ends_at: string;
    enabled: boolean;
  },
) =>
  request<AppBlock>(`/api/apps/${encodeURIComponent(appId)}`, {
    method: "PUT",
    body: JSON.stringify(body),
  });

export const removeAppBlock = (appId: string) =>
  request<{ removed: boolean; id: string }>(`/api/apps/${encodeURIComponent(appId)}`, {
    method: "DELETE",
  });

export const getRewrites = () => request<RewritesResponse>("/api/rewrites");

export const setRewrite = (name: string, address: string) =>
  request<{ rewrites: { name: string; address: string }[] }>("/api/rewrites", {
    method: "PUT",
    body: JSON.stringify({ name, address }),
  });

export const removeRewrite = (name: string) =>
  request<{ rewrites: { name: string; address: string }[] }>(
    `/api/rewrites/${encodeURIComponent(name)}`,
    { method: "DELETE" },
  );

export const getPolicy = () => request<PolicyResponse>("/api/policy");

export const savePolicy = (body: {
  rules?: string[];
  rules_enabled?: boolean;
  protection?: boolean;
  filter_lists?: string[];
  filter_lists_enabled?: boolean;
  safe_browsing?: boolean;
  parental_control?: boolean;
  youtube_safe_search?: boolean;
  engines_safe_search?: boolean;
}) => request<PolicyWriteResult>("/api/policy", { method: "PUT", body: JSON.stringify(body) });

export const syncPolicy = () => request<PolicySyncResult>("/api/policy/sync", { method: "POST" });

export const changeRule = (domain: string, action: "block" | "allow" | "clear") =>
  request<PolicyWriteResult & { domain: string; action: string }>("/api/policy/rule", {
    method: "POST",
    body: JSON.stringify({ domain, action }),
  });

export const addAccount = (username: string, password: string, label?: string) =>
  request<{
    account: AccountRow;
    store: AccountStoreStatus;
    serving: boolean;
    policy_sync?: PolicyAdoption;
  }>("/api/accounts", {
    method: "POST",
    body: JSON.stringify(label ? { username, password, label } : { username, password }),
  });

export const setAccountEnabled = (label: string, enabled: boolean) =>
  request<{ label: string; enabled: boolean; store: AccountStoreStatus; accounts: AccountRow[] }>(
    `/api/accounts/${encodeURIComponent(label)}`,
    { method: "PATCH", body: JSON.stringify({ enabled }) },
  );

export const removeAccount = (label: string) =>
  request<{ removed: string; store: AccountStoreStatus }>(
    `/api/accounts/${encodeURIComponent(label)}`,
    { method: "DELETE" },
  );

export const getAudit = (params: {
  source?: "live" | "history";
  limit?: number;
  before?: number | null;
  actor?: string;
  action?: string;
  outcome?: string;
  q?: string;
} = {}) => {
  const query = new URLSearchParams();
  query.set("source", params.source ?? "live");
  query.set("limit", String(params.limit ?? 100));
  if (params.before) query.set("before", String(params.before));
  if (params.actor) query.set("actor", params.actor);
  if (params.action) query.set("action", params.action);
  if (params.outcome) query.set("outcome", params.outcome);
  if (params.q) query.set("q", params.q);
  return request<AuditResponse>(`/api/audit?${query.toString()}`);
};

export const getTokens = () => request<TokensResponse>("/api/tokens");

export const createToken = (name: string, scope: string) =>
  request<{ token: { id: string; name: string; scope: string }; secret: string }>("/api/tokens", {
    method: "POST",
    body: JSON.stringify({ name, scope }),
  });

export const revokeToken = (id: string) =>
  request<{ revoked: boolean; id: string }>(`/api/tokens/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });

export const getAlerts = () => request<AlertsResponse>("/api/alerts");

export const sendTestAlert = () =>
  request<{ sent: boolean; error: string | null }>("/api/alerts/test", { method: "POST" });

export const sendDigest = () =>
  request<{ delivered: number; payload: { body: string; reasons: string[] } }>(
    "/api/alerts/digest",
    { method: "POST" },
  );

export const getDigestDevices = () => request<DigestDevicesResponse>("/api/digest/devices");

export const previewPolicy = (body: {
  rules?: string[];
  protection?: boolean;
  filter_lists?: string[];
  safe_browsing?: boolean;
  parental_control?: boolean;
}) => request<PolicyPreview>("/api/policy/preview", { method: "POST", body: JSON.stringify(body) });

export const getPolicyVersions = (limit = 25) =>
  request<PolicyVersionsResponse>(`/api/policy/versions?limit=${limit}`);

export const restorePolicyVersion = (id: number) =>
  request<{ restored: number; applied_to: string[] }>(`/api/policy/versions/${id}/restore`, {
    method: "POST",
  });

export const setDeviceTags = (deviceId: string, tags: string[]) =>
  request<DeviceConfig & { tags: string[] }>(
    `/api/devices/${encodeURIComponent(deviceId)}/tags`,
    { method: "PUT", body: JSON.stringify({ tags }) },
  );

export const bulkDevices = (body: {
  devices: string[];
  tags?: string[];
  rules?: string[];
  safe_search?: boolean;
  block_mode?: string;
  block_address?: string;
}) => request<BulkResult>("/api/devices/bulk", { method: "POST", body: JSON.stringify(body) });

export const importDevices = (csv: string, dryRun = false) =>
  request<ImportResult>("/api/devices/import", {
    method: "POST",
    body: JSON.stringify({ csv, dry_run: dryRun }),
  });

export const purgeQueryLog = (device: string) =>
  request<{ removed: number; device: string }>(
    `/api/query-log?device=${encodeURIComponent(device)}`,
    { method: "DELETE" },
  );

export function exportUrl(params: {
  source: "live" | "history";
  format: "ndjson" | "csv";
  device?: string;
  result?: string;
  q?: string;
}): string {
  const query = new URLSearchParams({ source: params.source, format: params.format });
  if (params.device) query.set("device", params.device);
  if (params.result) query.set("result", params.result);
  if (params.q) query.set("q", params.q);
  return `${API_BASE}/api/query-log/export?${query.toString()}`;
}

export const deviceExportUrl = () => `${API_BASE}/api/devices/export`;

export const getBackup = (accounts: boolean) =>
  request<{
    version: number;
    devices: { id: string; name: string }[];
    rewrites: Record<string, string>;
    accounts: { label: string; username: string; password: string }[];
  }>(`/api/backup?accounts=${accounts ? "true" : "false"}`);

export const postRestore = (body: unknown) =>
  request<{
    restored: { devices: number; rewrites: number; accounts: number };
    skipped: string[];
  }>("/api/restore", { method: "POST", body: JSON.stringify(body) });

export const getPush = () => request<PushResponse>("/api/push");

export const subscribePush = (body: {
  endpoint: string;
  p256dh: string;
  auth: string;
  label: string;
}) =>
  request<{ subscribed: boolean; devices: number }>("/api/push/subscribe", {
    method: "POST",
    body: JSON.stringify(body),
  });

export const unsubscribePush = (endpoint: string) =>
  request<{ removed: boolean; devices: number }>("/api/push/unsubscribe", {
    method: "POST",
    body: JSON.stringify({ endpoint }),
  });

export const testPush = () =>
  request<{ sent: number; failed: number }>("/api/push/test", { method: "POST" });

export type RuleEntryAction = "block" | "allow" | "clear";

export const getDomain = (name: string) =>
  request<DomainResponse>(`/api/domain?name=${encodeURIComponent(name)}`);

export const deviceRuleEntry = (
  deviceId: string,
  domain: string,
  action: RuleEntryAction,
  minutes?: number,
) =>
  request<{ id: string; rules: string[] }>(
    `/api/devices/${encodeURIComponent(deviceId)}/rules/entry`,
    {
      method: "POST",
      body: JSON.stringify(minutes === undefined ? { domain, action } : { domain, action, minutes }),
    },
  );

export const clearTemporaryEntry = (deviceId: string, domain: string) =>
  request<{ id: string; rules: string[] }>(
    `/api/devices/${encodeURIComponent(deviceId)}/rules/entry`,
    {
      method: "POST",
      body: JSON.stringify({ domain, action: "clear", temporary: true }),
    },
  );

export const groupRuleEntry = (groupId: string, domain: string, action: RuleEntryAction) =>
  request<RuleGroup>(`/api/rule-groups/${encodeURIComponent(groupId)}/rules/entry`, {
    method: "POST",
    body: JSON.stringify({ domain, action }),
  });
