"use client";

import { useEffect, useState } from "react";

import { changeRule, getPolicy, savePolicy, syncPolicy } from "@/lib/api";
import { useIsPhone } from "@/lib/media";
import { useSection } from "@/lib/nav";
import { PolicyHistory, PolicyPreviewPanel } from "./PolicyHistory";
import Apps, { AppsRow } from "./Apps";
import Blocklists, { HydraBlocklistsRow } from "./Blocklists";
import RuleGroups from "./RuleGroups";
import type {
  PolicyResponse,
  PolicySummary,
  PolicySyncResult,
  PolicyWriteResult,
  SettingDiff,
} from "@/lib/types";
import { useApi } from "@/lib/useApi";
import {
  Alert,
  Badge,
  Button,
  Card,
  CardHeader,
  CardSubtitle,
  CardTitle,
  Empty,
  ErrorNote,
  Faint,
  Group,
  PartialNote,
  Row,
  RowBlock,
  Screen,
  ScreenHeader,
  Scroller,
  Skeleton,
  Spacer,
  Spinner,
  Switch,
  TBody,
  TD,
  TH,
  THead,
  TR,
  Table,
  Textarea,
} from "./ui";

const PARENTAL_SWITCHES = [
  { key: "youtube_safe_search", label: "YouTube restricted mode" },
  { key: "engines_safe_search", label: "Search engine safe search" },
] as const;

interface Editor {
  data: PolicyResponse;
  policy: PolicySummary;
  total: number;
  busy: string;
  note: { tone: "ok" | "bad"; text: string } | null;
  draft: string;
  dirty: boolean;
  rules: string[];
  setDraft: (value: string) => void;
  setDirty: (value: boolean) => void;
  run: <R extends PolicyWriteResult>(
    key: string,
    what: string | ((result: R) => string),
    action: () => Promise<R>,
  ) => Promise<void>;
  syncLists: () => Promise<void>;
  syncAccounts: () => Promise<void>;
  refresh: () => void;
  onUnauthorized: () => void;
}

export default function Policy({ onUnauthorized }: { onUnauthorized: () => void }) {
  const phone = useIsPhone();
  const { data, error, loading, refresh } = useApi(getPolicy, [], { onUnauthorized });

  const [draft, setDraft] = useState("");
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState("");
  const [note, setNote] = useState<{ tone: "ok" | "bad"; text: string } | null>(null);

  useEffect(() => {
    if (data && !dirty) setDraft(data.rules_text);
  }, [data, dirty]);

  if (error) return <ErrorNote error={error} onRetry={refresh} />;
  if (loading && !data) return <Skeleton className="h-80" />;
  if (!data?.policy) {
    return (
      <Card>
        <PartialNote errors={data?.errors ?? {}} />
        <Empty>
          No account answered with its filtering settings, so there is no policy to show. The
          Accounts page will say why.
        </Empty>
      </Card>
    );
  }

  const { policy, accounts } = data;
  const total = accounts.length;

  const report = (result: PolicyWriteResult, what: string) => {
    const failed = Object.entries(result.errors);
    setNote(
      failed.length === 0
        ? {
            tone: "ok",
            text: `${what} on all ${result.applied_to.length} accounts${
              result.cache_cleared ? `, and ${result.cache_cleared} cached answers dropped` : ""
            }.`,
          }
        : {
            tone: "bad",
            text: `${what} on ${result.applied_to.join(", ")}, but not on ${failed
              .map(([label, message]) => `${label} (${message})`)
              .join("; ")}. The accounts are still out of sync.`,
          },
    );
    setDirty(false);
    refresh();
  };

  const run = async <R extends PolicyWriteResult>(
    key: string,
    what: string | ((result: R) => string),
    action: () => Promise<R>,
  ) => {
    setBusy(key);
    setNote(null);
    try {
      const result = await action();
      report(result, typeof what === "string" ? what : what(result));
    } catch (cause) {
      setNote({ tone: "bad", text: cause instanceof Error ? cause.message : "Failed" });
      throw cause;
    } finally {
      setBusy("");
    }
  };

  const syncLists = () =>
    run("lists", "Blocklists synced", () =>
      savePolicy({
        filter_lists: [...new Set(Object.values(policy.filter_lists.per_account).flat())],
      }),
    );

  const syncAccounts = () =>
    run(
      "sync",
      (result: PolicySyncResult) =>
        result.left.length > 0
          ? `Accounts synced (no majority on ${result.left.join(", ")})`
          : "Accounts synced",
      syncPolicy,
    );

  const rules = draft
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);

  const editor: Editor = {
    data,
    policy,
    total,
    busy,
    note,
    draft,
    dirty,
    rules,
    setDraft,
    setDirty,
    run,
    syncLists,
    syncAccounts,
    refresh,
    onUnauthorized,
  };

  return phone ? <RulesPhone {...editor} /> : <RulesDesktop {...editor} />;
}

function Notices({ policy, total, busy, note, errors, run }: Editor & { errors: Record<string, string> }) {
  return (
    <>
      {policy.unprotected_accounts.length > 0 && (
        <Alert tone="danger">
          <b>
            Filtering is switched off on {policy.unprotected_accounts.join(", ")}
            {policy.unprotected_accounts.length === 1 ? "" : " accounts"}.
          </b>{" "}
          Queries routed there are answered unfiltered &mdash; roughly{" "}
          {Math.round((policy.unprotected_accounts.length / Math.max(total, 1)) * 100)}% of your
          traffic, depending on how quota is spread.
          <div className="mt-2">
            <Button
              variant="primary"
              size="sm"
              disabled={busy !== ""}
              onClick={() =>
                run("protect", "Filtering enabled", () => savePolicy({ protection: true }))
              }
            >
              {busy === "protect" && <Spinner />}
              {busy === "protect" ? "Turning on…" : "Turn filtering on everywhere"}
            </Button>
          </div>
        </Alert>
      )}

      {note && (
        <Alert tone={note.tone === "ok" ? "info" : "danger"} role="status">
          {note.text}
        </Alert>
      )}

      <PartialNote errors={errors} />
    </>
  );
}

function outOfStep(policy: PolicySummary): boolean {
  return Object.keys(policy.outliers ?? {}).length > 0 || (policy.split ?? []).length > 0;
}

function DriftAlert({ policy, busy, syncAccounts }: Editor) {
  if (!outOfStep(policy)) return null;

  return (
    <Alert tone="warn">
      {Object.entries(policy.outliers).map(([label, sections]) => (
        <div key={label}>
          <b>{label}</b> filters differently: {sections.join(", ")}
        </div>
      ))}
      {policy.split.length > 0 && <div>No majority: {policy.split.join(", ")}</div>}
      <div className="mt-2">
        <Button
          variant="primary"
          size="sm"
          disabled={busy !== ""}
          onClick={() => void syncAccounts().catch(() => {})}
        >
          {busy === "sync" && <Spinner />}
          {busy === "sync" ? "Syncing…" : "Sync accounts"}
        </Button>
      </div>
    </Alert>
  );
}

function DriftGroup({ policy, busy, syncAccounts }: Editor) {
  if (!outOfStep(policy)) return null;

  return (
    <Group heading="Out of step">
      {Object.entries(policy.outliers).map(([label, sections]) => (
        <Row
          key={label}
          title={label}
          secondary={sections.join(", ")}
          trailing={<Badge tone="danger">differs</Badge>}
          chevron={false}
        />
      ))}
      {policy.split.length > 0 && (
        <Row
          title="No majority"
          secondary={policy.split.join(", ")}
          trailing={<Badge tone="warn">split</Badge>}
          chevron={false}
        />
      )}
      <Row
        title={busy === "sync" ? "Syncing…" : "Sync accounts"}
        tone="accent"
        chevron={false}
        disabled={busy !== ""}
        trailing={busy === "sync" ? <Spinner /> : undefined}
        onClick={() => void syncAccounts().catch(() => {})}
      />
    </Group>
  );
}

function RulesDesktop(editor: Editor) {
  const { data, policy, total, busy, draft, dirty, rules, setDraft, setDirty, run, syncLists } =
    editor;

  return (
    <div className="flex flex-col gap-3.5">
      <Notices {...editor} errors={data.errors} />

      <DriftAlert {...editor} />

      <Card>
        <CardHeader>
          <CardTitle>Agreement across {total} accounts</CardTitle>
          <Spacer />
          {policy.in_sync ? (
            <Badge tone="ok">in sync</Badge>
          ) : (
            <Badge tone="danger">{policy.drifting.length} settings differ</Badge>
          )}
        </CardHeader>

        <Scroller>
          <Table>
            <THead>
              <TR>
                <TH>Setting</TH>
                <TH>State</TH>
                <TH className="max-wide:hidden">Detail</TH>
                <TH>
                  <span className="sr-only">Actions</span>
                </TH>
              </TR>
            </THead>
            <TBody>
              <SyncRow
                label="Filtering enabled"
                inSync={policy.protection.in_sync}
                detail={describe(policy.protection.per_account, (value) => (value ? "on" : "off"))}
                toggle={
                  <RowToggle
                    busy={busy}
                    keyName="protection"
                    label="Filtering enabled"
                    value={policy.protection.value ?? majority(policy.protection.per_account)}
                    onRun={(value) =>
                      run("protection", `Filtering ${value ? "enabled" : "disabled"}`, () =>
                        savePolicy({ protection: value }),
                      )
                    }
                  />
                }
              />
              <SyncRow
                label="AdGuard blocklists"
                inSync={policy.filter_lists.in_sync}
                detail={describe(
                  policy.filter_lists.per_account,
                  (value) => `${value.length} enabled`,
                )}
                fix={
                  <Button size="sm" disabled={busy !== ""} onClick={syncLists}>
                    {busy === "lists" ? <Spinner /> : "Match"}
                  </Button>
                }
              />
              <SyncRow
                label="Blocklists switch"
                inSync={policy.filter_lists_enabled.in_sync}
                detail={describe(policy.filter_lists_enabled.per_account, (value) =>
                  value ? "on" : "off",
                )}
                toggle={
                  <RowToggle
                    busy={busy}
                    keyName="filter_lists_enabled"
                    label="Blocklists switch"
                    value={
                      policy.filter_lists_enabled.value ??
                      majority(policy.filter_lists_enabled.per_account)
                    }
                    onRun={(value) =>
                      run("filter_lists_enabled", `Blocklists ${value ? "on" : "off"}`, () =>
                        savePolicy({ filter_lists_enabled: value }),
                      )
                    }
                  />
                }
              />
              <SyncRow
                label="Custom rules switch"
                inSync={policy.rules_enabled.in_sync}
                detail={describe(policy.rules_enabled.per_account, (value) =>
                  value ? "on" : "off",
                )}
                toggle={
                  <RowToggle
                    busy={busy}
                    keyName="rules_enabled"
                    label="Custom rules switch"
                    value={policy.rules_enabled.value ?? majority(policy.rules_enabled.per_account)}
                    onRun={(value) =>
                      run("rules_enabled", `Custom rules ${value ? "on" : "off"}`, () =>
                        savePolicy({ rules_enabled: value }),
                      )
                    }
                  />
                }
              />
              <SyncRow
                label="Safe browsing"
                inSync={policy.safe_browsing.in_sync}
                detail={describe(policy.safe_browsing.per_account, (value) =>
                  value ? "on" : "off",
                )}
                toggle={
                  <RowToggle
                    busy={busy}
                    keyName="safe_browsing"
                    label="Safe browsing"
                    value={
                      policy.safe_browsing.value ?? majority(policy.safe_browsing.per_account)
                    }
                    onRun={(value) =>
                      run("safe_browsing", `Safe browsing ${value ? "enabled" : "disabled"}`, () =>
                        savePolicy({ safe_browsing: value }),
                      )
                    }
                  />
                }
              />
              <SyncRow
                label="Parental control"
                inSync={policy.parental_control.in_sync}
                detail={describe(policy.parental_control.per_account, (value) =>
                  value ? "on" : "off",
                )}
                toggle={
                  <RowToggle
                    busy={busy}
                    keyName="parental_control"
                    label="Parental control"
                    value={
                      policy.parental_control.value ??
                      majority(policy.parental_control.per_account)
                    }
                    onRun={(value) =>
                      run(
                        "parental_control",
                        `Parental control ${value ? "enabled" : "disabled"}`,
                        () => savePolicy({ parental_control: value }),
                      )
                    }
                  />
                }
              />
              {PARENTAL_SWITCHES.map(({ key, label }) => {
                const diff = policy[key];
                if (!diff) return null;
                return (
                  <SyncRow
                    key={key}
                    label={label}
                    inSync={diff.in_sync}
                    detail={describe(diff.per_account, (value) => (value ? "on" : "off"))}
                    toggle={
                      <RowToggle
                        busy={busy}
                        keyName={key}
                        label={label}
                        value={diff.value ?? majority(diff.per_account)}
                        onRun={(value) =>
                          run(key, `${label} ${value ? "on" : "off"}`, () =>
                            savePolicy({ [key]: value }),
                          )
                        }
                      />
                    }
                  />
                );
              })}
            </TBody>
          </Table>
        </Scroller>

        {!policy.filter_lists.in_sync && (
          <Alert tone="warn" className="mt-3">
            The accounts have different blocklists enabled, so a domain on one account&apos;s list
            is blocked only when a query happens to land there. Syncing uses the union &mdash;
            every list enabled anywhere, enabled everywhere.
            <div className="mt-2">
              <Button disabled={busy !== ""} onClick={syncLists}>
                {busy === "lists" && <Spinner />}
                {busy === "lists" ? "Syncing…" : "Use the same blocklists everywhere"}
              </Button>
            </div>
            <div className="mt-2 text-xs">
              {Object.entries(policy.filter_lists.per_account).map(([label, ids]) => (
                <div key={label}>
                  <b>{label}</b>:{" "}
                  {ids.map((id) => policy.filter_list_names[id] ?? id).join(", ")}
                </div>
              ))}
            </div>
          </Alert>
        )}
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Custom rules</CardTitle>
          {policy.rules.in_sync ? (
            <Badge tone="ok">same everywhere</Badge>
          ) : (
            <Badge tone="danger">
              {policy.rules.rules.filter((row) => !row.everywhere).length} of{" "}
              {policy.rules.rules.length} missing somewhere
            </Badge>
          )}
          <Spacer />
          <Faint className="tabular-nums">
            {rules.length} / {data.rules_max}
          </Faint>
        </CardHeader>

        <div className="mt-1.5 mb-3">
          <Faint className="text-xs">
            One rule per line. Saving replaces the list on every account.
          </Faint>
        </div>

        <Textarea
          rows={14}
          spellCheck={false}
          value={draft}
          onChange={(event) => {
            setDraft(event.target.value);
            setDirty(true);
          }}
          aria-label="Custom filtering rules"
        />

        <div className="mt-2.5 flex flex-wrap items-center gap-2">
          <Button
            variant="primary"
            disabled={busy !== "" || rules.length > data.rules_max}
            onClick={() =>
              run("rules", "Rules saved", () => savePolicy({ rules, rules_enabled: true }))
            }
          >
            {busy === "rules" && <Spinner />}
            {busy === "rules" ? "Saving to every account…" : `Save to all ${total} accounts`}
          </Button>
          {dirty && (
            <Button
              disabled={busy !== ""}
              onClick={() => {
                setDraft(data.rules_text);
                setDirty(false);
              }}
            >
              Discard changes
            </Button>
          )}
          {rules.length > data.rules_max && (
            <Faint>AdGuard allows {data.rules_max} rules per account.</Faint>
          )}
        </div>

        <PolicyPreviewPanel rules={rules} />

        {!policy.rules.in_sync && (
          <div className="mt-5">
            <CardSubtitle>Rules that are not everywhere</CardSubtitle>

            <Scroller>
              <Table>
                <THead>
                  <TR>
                    <TH>Rule</TH>
                    <TH>Present on</TH>
                  </TR>
                </THead>
                <TBody>
                  {policy.rules.rules
                    .filter((row) => !row.everywhere)
                    .map((row) => (
                      <TR key={row.rule}>
                        <TD className="max-w-80 truncate font-mono text-2xs" title={row.rule}>
                          {row.rule}
                        </TD>
                        <TD className="whitespace-nowrap">
                          <span className="flex flex-wrap items-center gap-1.5">
                            <Badge tone="warn">
                              {row.accounts.length} of {total}
                            </Badge>
                            <Faint>{row.accounts.join(", ")}</Faint>
                          </span>
                        </TD>
                      </TR>
                    ))}
                </TBody>
              </Table>
            </Scroller>
          </div>
        )}
      </Card>

      <RuleGroups onUnauthorized={editor.onUnauthorized} />

      <Apps onUnauthorized={editor.onUnauthorized} />

      <Blocklists onUnauthorized={editor.onUnauthorized} />

      <PolicyHistory onRestored={editor.refresh} onUnauthorized={editor.onUnauthorized} />
    </div>
  );
}

function RulesPhone(editor: Editor) {
  const { path, open } = useSection("rules");
  const { data, policy, total, busy, rules, run, syncLists } = editor;

  if (path[0] === "custom") return <CustomRulesScreen {...editor} />;
  if (path[0] === "blocklists") return <BlocklistsScreen {...editor} />;
  if (path[0] === "history") return <HistoryScreen {...editor} />;
  if (path[0] === "groups") return <RuleGroups onUnauthorized={editor.onUnauthorized} />;
  if (path[0] === "lists") return <Blocklists onUnauthorized={editor.onUnauthorized} />;
  if (path[0] === "apps") return <Apps onUnauthorized={editor.onUnauthorized} />;

  const lists = policy.filter_lists;

  return (
    <Screen>
      <Notices {...editor} errors={data.errors} />

      <DriftGroup {...editor} />

      <Group
        heading={`Applied to all ${total} accounts`}
        info="AdGuard replaces the whole settings object on a write, so each change here is read, edited and written back to every account in turn. A setting that is on for one account only filters the traffic that happens to land there."
      >
        <SettingRow
          label="Filtering"
          diff={policy.protection}
          busy={busy}
          keyName="protection"
          onRun={(value) =>
            run("protection", `Filtering ${value ? "enabled" : "disabled"}`, () =>
              savePolicy({ protection: value }),
            )
          }
        />
        <SettingRow
          label="Safe browsing"
          diff={policy.safe_browsing}
          busy={busy}
          keyName="safe_browsing"
          onRun={(value) =>
            run("safe_browsing", `Safe browsing ${value ? "enabled" : "disabled"}`, () =>
              savePolicy({ safe_browsing: value }),
            )
          }
        />
        <SettingRow
          label="Parental control"
          diff={policy.parental_control}
          busy={busy}
          keyName="parental_control"
          onRun={(value) =>
            run("parental_control", `Parental control ${value ? "enabled" : "disabled"}`, () =>
              savePolicy({ parental_control: value }),
            )
          }
        />
        {PARENTAL_SWITCHES.map(({ key, label }) => {
          const diff = policy[key];
          if (!diff) return null;
          return (
            <SettingRow
              key={key}
              label={label}
              diff={diff}
              busy={busy}
              keyName={key}
              onRun={(value) =>
                run(key, `${label} ${value ? "on" : "off"}`, () => savePolicy({ [key]: value }))
              }
            />
          );
        })}
        <SettingRow
          label="Blocklists switch"
          diff={policy.filter_lists_enabled}
          busy={busy}
          keyName="filter_lists_enabled"
          onRun={(value) =>
            run("filter_lists_enabled", `Blocklists ${value ? "on" : "off"}`, () =>
              savePolicy({ filter_lists_enabled: value }),
            )
          }
        />
        <SettingRow
          label="Custom rules switch"
          diff={policy.rules_enabled}
          busy={busy}
          keyName="rules_enabled"
          onRun={(value) =>
            run("rules_enabled", `Custom rules ${value ? "on" : "off"}`, () =>
              savePolicy({ rules_enabled: value }),
            )
          }
        />
        <Row
          title="AdGuard blocklists"
          secondary={agreement(lists)}
          value={`${blocklistRows(policy).length} enabled`}
          onClick={() => open("blocklists")}
        />
      </Group>

      {!policy.filter_lists.in_sync && (
        <Alert tone="warn">
          A domain on one account&apos;s list is blocked only when a query happens to land there.
          Syncing enables every list that is enabled anywhere, everywhere.
          <div className="mt-2">
            <Button disabled={busy !== ""} onClick={syncLists}>
              {busy === "lists" && <Spinner />}
              {busy === "lists" ? "Syncing…" : "Use the same blocklists everywhere"}
            </Button>
          </div>
        </Alert>
      )}

      <Group
        heading="Rules"
        info="Rules here reach every account. A rule that should apply to one device belongs on that device instead, where it costs no allowance."
      >
        <Row
          title="Custom rules"
          secondary={
            policy.rules.in_sync
              ? "on every account"
              : `${policy.rules.rules.filter((row) => !row.everywhere).length} missing from at least one account`
          }
          value={`${rules.length} ${rules.length === 1 ? "rule" : "rules"}`}
          onClick={() => open("custom")}
        />
        <Row
          title="History"
          secondary="Every accepted policy write, one tap to undo"
          onClick={() => open("history")}
        />
      </Group>

      <Group
        heading="On the resolver"
        info="Checked against the device that asked, before the cache and before any account, so these cost no allowance and never reach AdGuard."
      >
        <Row
          title="Schedules"
          secondary="Names blocked on tagged devices, during the hours you choose"
          onClick={() => open("groups")}
        />
        <AppsRow onOpen={() => open("apps")} onUnauthorized={editor.onUnauthorized} />
        <HydraBlocklistsRow onOpen={() => open("lists")} onUnauthorized={editor.onUnauthorized} />
      </Group>
    </Screen>
  );
}

function CustomRulesScreen(editor: Editor) {
  const { data, policy, total, busy, draft, dirty, rules, setDraft, setDirty, run } = editor;
  const tooMany = rules.length > data.rules_max;

  return (
    <Screen>
      <ScreenHeader
        title="Custom rules"
        actions={
          <Button
            variant="primary"
            size="sm"
            disabled={busy !== "" || tooMany || !dirty}
            onClick={() =>
              run("rules", "Rules saved", () => savePolicy({ rules, rules_enabled: true }))
            }
          >
            {busy === "rules" && <Spinner />}
            {busy === "rules" ? "Saving…" : "Save"}
          </Button>
        }
      />

      <Notices {...editor} errors={{}} />

      <section className="flex min-w-0 flex-col">
        <div className="flex min-w-0 items-center gap-2 px-1 pb-1.5">
          <h2 className="m-0 min-w-0 flex-1 truncate text-xs font-medium tracking-[0.06em] text-muted uppercase tabular-nums">
            {rules.length} of {data.rules_max}
          </h2>
          {dirty && (
            <Button
              size="xs"
              disabled={busy !== ""}
              onClick={() => {
                setDraft(data.rules_text);
                setDirty(false);
              }}
            >
              Discard
            </Button>
          )}
        </div>

        <Textarea
          rows={12}
          spellCheck={false}
          value={draft}
          onChange={(event) => {
            setDraft(event.target.value);
            setDirty(true);
          }}
          aria-label="Custom filtering rules"
          className="rounded-xl"
        />

        <p className="mt-1.5 mb-0 px-1 text-xs leading-snug text-muted">
          One rule per line. Saving replaces the list on all {total} accounts.
        </p>

        {tooMany && (
          <p className="mt-1.5 mb-0 px-1 text-xs text-danger-ink">
            AdGuard allows {data.rules_max} rules per account.
          </p>
        )}

        <PolicyPreviewPanel rules={rules} />
      </section>

      {!policy.rules.in_sync && (
        <Group
          heading="Not everywhere"
          info="Each of these currently applies to only part of your traffic. Saving fixes all of them at once."
        >
          {policy.rules.rules
            .filter((row) => !row.everywhere)
            .map((row) => (
              <Row
                key={row.rule}
                title={row.rule}
                mono
                wrap
                secondary={`on ${row.accounts.join(", ")}`}
                trailing={
                  <Badge tone="warn">
                    {row.accounts.length} of {total}
                  </Badge>
                }
              />
            ))}
        </Group>
      )}
    </Screen>
  );
}

function BlocklistsScreen(editor: Editor) {
  const { policy, total, busy, syncLists } = editor;
  const lists = policy.filter_lists;
  const rows = blocklistRows(policy);

  return (
    <Screen>
      <ScreenHeader title="AdGuard blocklists" />

      <Notices {...editor} errors={{}} />

      <Group
        heading={`${rows.length} enabled`}
        info="One list applies to every account, so a domain on it is blocked whichever account the query lands on. Anything marked below is on only some of them, which means it filters only the traffic that happens to go there."
      >
        {rows.map((row) => (
          <Row
            key={row.id}
            title={row.name}
            secondary={row.everywhere ? undefined : `missing from ${row.missing.join(", ")}`}
            trailing={
              row.everywhere ? undefined : (
                <Badge tone="warn">
                  {row.on.length} of {total}
                </Badge>
              )
            }
            chevron={false}
          />
        ))}

        {rows.length === 0 && (
          <RowBlock>
            <Empty>No blocklist is enabled on any account.</Empty>
          </RowBlock>
        )}
      </Group>

      {!lists.in_sync && (
        <Group info="Syncing uses the union — every list enabled anywhere, enabled everywhere.">
          <Row
            title={busy === "lists" ? "Syncing…" : "Use the same blocklists everywhere"}
            tone="accent"
            chevron={false}
            disabled={busy !== ""}
            trailing={busy === "lists" ? <Spinner /> : undefined}
            onClick={() => void syncLists()}
          />
        </Group>
      )}
    </Screen>
  );
}

function blocklistRows(policy: PolicySummary) {
  const per = policy.filter_lists.per_account;
  const accounts = Object.keys(per);

  return [...new Set(Object.values(per).flat())]
    .map((id) => {
      const on = accounts.filter((label) => per[label].includes(id));
      return {
        id,
        name: policy.filter_list_names[id] ?? id,
        on,
        missing: accounts.filter((label) => !on.includes(label)),
        everywhere: on.length === accounts.length,
      };
    })
    .sort((a, b) => a.name.localeCompare(b.name));
}

function HistoryScreen({ refresh, onUnauthorized }: Editor) {
  return (
    <Screen>
      <ScreenHeader title="History" />
      <PolicyHistory onRestored={refresh} onUnauthorized={onUnauthorized} />
    </Screen>
  );
}

function SettingRow({
  label,
  diff,
  busy,
  keyName,
  onRun,
}: {
  label: string;
  diff: SettingDiff<boolean>;
  busy: string;
  keyName: string;
  onRun: (value: boolean) => Promise<unknown>;
}) {
  return (
    <Row
      title={label}
      secondary={agreement(diff)}
      trailing={
        <RowToggle
          busy={busy}
          keyName={keyName}
          label={label}
          value={diff.value ?? majority(diff.per_account)}
          onRun={onRun}
        />
      }
    />
  );
}

function agreement<T>(diff: SettingDiff<T>): string {
  if (diff.in_sync) return "same everywhere";

  const values = Object.values(diff.per_account).map((value) => JSON.stringify(value));
  const counts = new Map<string, number>();
  for (const value of values) counts.set(value, (counts.get(value) ?? 0) + 1);

  const agreed = Math.max(0, ...counts.values());
  return `differs — ${values.length - agreed} of ${values.length}`;
}

function SyncRow({
  label,
  inSync,
  detail,
  fix,
  toggle,
}: {
  label: string;
  inSync: boolean;
  detail: string;
  fix?: React.ReactNode;
  toggle?: React.ReactNode;
}) {
  return (
    <TR>
      <TD className="font-medium">{label}</TD>
      <TD>{inSync ? <Badge tone="ok">same</Badge> : <Badge tone="danger">differs</Badge>}</TD>
      <TD className="text-xs text-subtle max-wide:hidden">{detail}</TD>
      <TD numeric>{toggle ?? (!inSync ? fix : null)}</TD>
    </TR>
  );
}

function RowToggle({
  busy,
  keyName,
  label,
  value,
  onRun,
}: {
  busy: string;
  keyName: string;
  label: string;
  value: boolean;
  onRun: (value: boolean) => Promise<unknown>;
}) {
  const [on, setOn] = useState(value);
  useEffect(() => setOn(value), [value]);

  return (
    <span className="inline-flex items-center gap-2">
      {busy === keyName && <Spinner />}
      <Switch
        aria-label={label}
        checked={on}
        disabled={busy !== ""}
        onCheckedChange={(wanted) => {
          setOn(wanted);
          void onRun(wanted).catch(() => setOn(!wanted));
        }}
      />
    </span>
  );
}

function majority(perAccount: Record<string, boolean>): boolean {
  const values = Object.values(perAccount);
  return values.filter(Boolean).length * 2 >= values.length;
}

function describe<T>(perAccount: Record<string, T>, render: (value: T) => string): string {
  return Object.entries(perAccount)
    .map(([label, value]) => `${label} ${render(value)}`)
    .join(" · ");
}

export function BlockButton({
  domain,
  blocked,
  onDone,
  onResult,
  className,
}: {
  domain: string;
  blocked: boolean;
  onDone?: () => void;
  onResult?: (errors: Record<string, string>) => void;
  className?: string;
}) {
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<"blocked" | "allowed" | null>(null);
  const [failed, setFailed] = useState("");

  const act = async (action: "block" | "allow") => {
    setBusy(true);
    setFailed("");
    try {
      const result = await changeRule(domain, action);
      const missed = Object.keys(result.errors);
      if (missed.length > 0) {
        setFailed(`not on ${missed.join(", ")}`);
      } else {
        setDone(action === "block" ? "blocked" : "allowed");
      }
      onResult?.(result.errors);
      onDone?.();
    } catch (cause) {
      setFailed(cause instanceof Error ? cause.message.slice(0, 60) : "failed");
    } finally {
      setBusy(false);
    }
  };

  if (done) {
    return <Faint>{done} everywhere</Faint>;
  }

  return (
    <span className="inline-flex items-center gap-1.5">
      <Button
        size="xs"
        className={className}
        disabled={busy}
        title={blocked ? `Allow ${domain} on every account` : `Block ${domain} on every account`}
        onClick={() => void act(blocked ? "allow" : "block")}
      >
        {busy ? <Spinner /> : blocked ? "Allow" : "Block"}
      </Button>
      {failed && <Faint title={failed}>{failed}</Faint>}
    </span>
  );
}
