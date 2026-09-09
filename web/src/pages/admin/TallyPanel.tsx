import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError } from "../../lib/api";
import { downloadFile } from "../../lib/download";
import type {
  FirmTallyShop,
  KnownLedger,
  LedgerMapPatch,
  TallyCompany,
  TallySyncJob,
} from "../../lib/types";
import { adminApi } from "./api";

/** The XML download endpoint needs the bearer header — a plain <a href> can't
 *  carry one, so fetch-as-blob-and-save (same as the invoice-PDF download). */
function downloadJobXml(firmId: string, jobId: string): void {
  void downloadFile(
    `/admin/firms/${firmId}/tally/sync-jobs/${jobId}/xml`,
    `tally-masters-${jobId}.xml`,
  ).catch(() => {
    /* surfaced by the network tab; a toast system would go here */
  });
}

/**
 * Two sections on the operator console's firm-detail pane, stacked below
 * WhatsApp (same `border-b border-line py-5` idiom):
 *
 *   Agent — this firm's companion-agent identity. One per firm; the key it
 *           authenticates with is generated here and shown once. Every
 *           shop-side capability (Tally sync, backup, health) rides on it.
 *   Tally — link the firm's Tally company, map its ledgers, pull masters.
 *
 * Tally is disabled until the agent is provisioned.
 */
export function TallyPanel({ firmId, firmName }: { firmId: string; firmName: string }) {
  const qc = useQueryClient();
  const agentKey = ["admin-firm-agent", firmId];
  const agent = useQuery({
    queryKey: agentKey,
    queryFn: () => adminApi.getFirmAgent(firmId),
    // Live health — cheap poll while this pane is open so a shop coming
    // online (or Tally being switched to Server mode) shows up without a
    // manual refresh.
    refetchInterval: (q) => (q.state.data?.provisioned ? 20_000 : false),
  });
  const refreshAgent = () => void qc.invalidateQueries({ queryKey: agentKey });

  return (
    <>
      <AgentSection
        firmId={firmId}
        firmName={firmName}
        agent={agent.data ?? null}
        loading={agent.isLoading}
        onChanged={refreshAgent}
      />
      <TallySection
        firmId={firmId}
        agentReady={!!agent.data?.provisioned}
        tallyStatus={agent.data?.tally_status ?? null}
        agentOnline={!!agent.data?.agent_online}
      />
    </>
  );
}

// -------------------------------------------------------------------------
// Agent section — provision / rotate key + reachability
// -------------------------------------------------------------------------

function agentAge(lastCheckin: string | null): {
  label: string;
  tone: "ok" | "warn" | "bad";
} {
  if (!lastCheckin) return { label: "no check-in yet", tone: "warn" };
  const min = Math.round((Date.now() - new Date(lastCheckin).getTime()) / 60000);
  if (min < 3) return { label: `checked in ${min <= 0 ? "just now" : `${min}m ago`}`, tone: "ok" };
  if (min < 180) return { label: `checked in ${min}m ago`, tone: "ok" };
  const hr = Math.round(min / 60);
  if (hr < 48) return { label: `no check-in for ${hr}h`, tone: "warn" };
  return { label: `no check-in for ${Math.round(hr / 24)}d`, tone: "bad" };
}

function backupHealth(
  lastUploadAt: string | null,
  uploadCount: number,
): { label: string; tone: "ok" | "warn" | "bad" } {
  if (!lastUploadAt) {
    return uploadCount === 0
      ? { label: "no backup uploaded yet", tone: "warn" }
      : { label: "no backup uploaded yet", tone: "bad" };
  }
  const hr = (Date.now() - new Date(lastUploadAt).getTime()) / 3_600_000;
  // Mirrors BackupHealthMonitorModule's own ExpectedIntervalHours default (26h).
  if (hr < 26) return { label: `last backup ${timeAgo(lastUploadAt)}`, tone: "ok" };
  return { label: `no backup since ${timeAgo(lastUploadAt)} — check Tally's scheduled backup`, tone: "bad" };
}

function tallyStatusLabel(status: FirmTallyShop["tally_status"]): {
  label: string;
  tone: "ok" | "warn" | "bad";
} {
  switch (status) {
    case "connected":
      return { label: "Connected", tone: "ok" };
    case "refused":
      return { label: "Not reachable — Tally is closed or not in Server mode", tone: "bad" };
    case "no_company":
      return { label: "Not reachable — no company is open in Tally", tone: "bad" };
    case "unknown":
      return { label: "Not reachable", tone: "bad" };
    default:
      return { label: "Not reported yet", tone: "warn" };
  }
}

function AgentSection({
  firmId,
  firmName,
  agent,
  loading,
  onChanged,
}: {
  firmId: string;
  firmName: string;
  agent: FirmTallyShop | null;
  loading: boolean;
  onChanged: () => void;
}) {
  const [err, setErr] = useState<string | null>(null);
  const [downloading, setDownloading] = useState(false);

  const provision = useMutation({
    mutationFn: () => adminApi.provisionFirmAgent(firmId),
    onSuccess: () => {
      setErr(null);
      onChanged();
    },
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Could not set up the agent"),
  });
  const rotate = useMutation({
    mutationFn: () => adminApi.rotateFirmAgentKey(firmId),
    onSuccess: () => {
      setErr(null);
      onChanged();
    },
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Could not rotate"),
  });

  const download = async () => {
    setErr(null);
    setDownloading(true);
    try {
      const slug = firmName.replace(/[^\w]+/g, "-").replace(/^-+|-+$/g, "") || "shop";
      await adminApi.downloadFirmAgentInstaller(firmId, `tally-agent-${slug}.zip`);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Download failed");
    } finally {
      setDownloading(false);
    }
  };

  const provisioned = !!agent?.provisioned;
  const checkin = provisioned ? agentAge(agent!.last_checkin_at) : null;
  const tally = provisioned ? tallyStatusLabel(agent!.tally_status) : null;
  const backup = provisioned
    ? backupHealth(agent!.last_upload_at, agent!.upload_count)
    : null;

  return (
    <div className="border-b border-line py-5">
      <div className="flex items-baseline justify-between">
        <h3 className="text-xs font-semibold uppercase tracking-[0.06em] text-muted">
          Tally Agent
        </h3>
        {!provisioned && (
          <span className="rounded-full bg-line px-2 py-0.5 text-[11px] font-semibold text-muted">
            Not set up
          </span>
        )}
      </div>

      {loading ? (
        <p className="mt-2 text-sm text-muted">Loading…</p>
      ) : !provisioned ? (
        <div className="mt-3">
          <p className="max-w-prose text-xs text-muted">
            The companion agent syncs this firm's Tally masters into Metal ERP.
            One click generates a ready-to-run installer for the shop — no
            configuration, nothing to type.
          </p>
          <button
            className="btn-primary mt-3"
            disabled={provision.isPending}
            onClick={() => provision.mutate()}
          >
            {provision.isPending ? "Setting up…" : "Set up Tally sync"}
          </button>
        </div>
      ) : (
        <>
          <div className="mt-3">
            <p className="text-xs font-semibold uppercase tracking-[0.06em] text-muted">
              Installer
            </p>
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <button
                className="btn-primary"
                disabled={downloading || !agent!.installer_ready}
                onClick={() => void download()}
              >
                {downloading ? "Downloading…" : "↓ Download installer"}
              </button>
              <button
                className="rounded border border-line px-2.5 py-1 text-xs enabled:hover:bg-ground disabled:opacity-40"
                disabled={rotate.isPending}
                onClick={() => rotate.mutate()}
              >
                {rotate.isPending ? "Rotating…" : "Rotate key & rebuild"}
              </button>
            </div>
            {!agent!.installer_ready && (
              <p className="mt-1.5 text-[11px] text-warn">
                Agent identity created, but the installer hasn't built yet —
                the agent build may not be published to this environment. Try
                rotating the key once it is.
              </p>
            )}
            <p className="mt-1.5 max-w-prose text-[11px] text-muted">
              Download and send this zip to the shop — right-click{" "}
              <span className="font-mono">install.ps1</span> and run it, no
              typing needed. Rotating replaces the key and invalidates any
              copy already sent out.
            </p>
          </div>

          <div className="mt-4">
            <p className="text-xs font-semibold uppercase tracking-[0.06em] text-muted">
              Live health
            </p>
            <div className="mt-2 grid grid-cols-3 gap-2">
              <div className="rounded-lg border border-line bg-ground/40 p-2.5">
                <p className="text-[10px] uppercase tracking-[0.05em] text-muted">
                  Agent → Fleek
                </p>
                <p className="mt-1 flex items-center gap-1.5 text-xs font-semibold">
                  <span
                    className={`h-1.5 w-1.5 rounded-full ${
                      checkin?.tone === "ok" ? "bg-ok" : checkin?.tone === "warn" ? "bg-warn" : "bg-danger"
                    }`}
                  />
                  {checkin?.tone === "ok" ? "Online" : "Offline"}
                </p>
                <p className="mt-0.5 text-[11px] text-muted">{checkin?.label}</p>
              </div>
              <div className="rounded-lg border border-line bg-ground/40 p-2.5">
                <p className="text-[10px] uppercase tracking-[0.05em] text-muted">
                  Agent → TallyPrime
                </p>
                <p className="mt-1 flex items-center gap-1.5 text-xs font-semibold">
                  <span
                    className={`h-1.5 w-1.5 rounded-full ${
                      tally?.tone === "ok" ? "bg-ok" : tally?.tone === "warn" ? "bg-warn" : "bg-danger"
                    }`}
                  />
                  {tally?.tone === "ok" ? "Connected" : "Not reachable"}
                </p>
                <p className="mt-0.5 text-[11px] text-muted">{tally?.label}</p>
              </div>
              <div className="rounded-lg border border-line bg-ground/40 p-2.5">
                <p className="text-[10px] uppercase tracking-[0.05em] text-muted">
                  Cloud backup
                </p>
                <p className="mt-1 flex items-center gap-1.5 text-xs font-semibold">
                  <span
                    className={`h-1.5 w-1.5 rounded-full ${
                      backup?.tone === "ok" ? "bg-ok" : backup?.tone === "warn" ? "bg-warn" : "bg-danger"
                    }`}
                  />
                  {backup?.tone === "ok" ? "Up to date" : "Attention"}
                </p>
                <p className="mt-0.5 text-[11px] text-muted">
                  {backup?.label}
                  {agent!.upload_count > 0 && ` · ${agent!.upload_count} total`}
                </p>
              </div>
            </div>
          </div>
        </>
      )}

      {err && <p className="err mt-2">{err}</p>}
    </div>
  );
}

// -------------------------------------------------------------------------
// Tally section
// -------------------------------------------------------------------------

function TallySection({
  firmId,
  agentReady,
  tallyStatus,
  agentOnline,
}: {
  firmId: string;
  agentReady: boolean;
  tallyStatus: FirmTallyShop["tally_status"];
  agentOnline: boolean;
}) {
  const qc = useQueryClient();
  const companyKey = ["admin-firm-tally", firmId];
  const company = useQuery({
    queryKey: companyKey,
    queryFn: () => adminApi.getTallyCompany(firmId),
    retry: (n, e) => !(e instanceof ApiError && e.status === 404) && n < 2,
  });
  const linked = company.data ?? null;
  const refresh = () => void qc.invalidateQueries({ queryKey: companyKey });

  return (
    <div className="border-b border-line py-5">
      <div className="flex items-baseline justify-between">
        <h3 className="text-xs font-semibold uppercase tracking-[0.06em] text-muted">
          Tally
        </h3>
        <span
          className={`rounded-full px-2 py-0.5 text-[11px] font-semibold ${
            linked ? "bg-ok/15 text-ok" : "bg-line text-muted"
          }`}
        >
          {linked ? "linked" : "not linked"}
        </span>
      </div>

      {!agentReady ? (
        <p className="mt-2 text-xs text-muted">
          Set up the shop agent above first.
        </p>
      ) : company.isLoading ? (
        <p className="mt-2 text-sm text-muted">Loading…</p>
      ) : (
        <>
          <CompanyBlock firmId={firmId} company={linked} onSaved={refresh} />
          {linked && (
            <>
              <PullBlock
                firmId={firmId}
                company={linked}
                onPulled={refresh}
                tallyStatus={tallyStatus}
                agentOnline={agentOnline}
              />
              <LedgerMapBlock firmId={firmId} company={linked} onSaved={refresh} />
              <RecentPulls firmId={firmId} />
            </>
          )}
        </>
      )}
    </div>
  );
}

function CompanyBlock({
  firmId,
  company,
  onSaved,
}: {
  firmId: string;
  company: TallyCompany | null;
  onSaved: () => void;
}) {
  const [name, setName] = useState(company?.company_name ?? "");
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => setName(company?.company_name ?? ""), [company]);

  const save = useMutation({
    mutationFn: () =>
      adminApi.upsertTallyCompany(firmId, { company_name: name.trim() }),
    onSuccess: onSaved,
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Save failed"),
  });

  const dirty = name.trim() !== (company?.company_name ?? "");

  return (
    <div className="mt-3">
      <div>
        <label className="label">Company name in Tally</label>
        <input
          className="field w-64"
          value={name}
          onChange={(e) => setName(e.target.value)}
          onBlur={() => {
            setErr(null);
            if (dirty && name.trim()) save.mutate();
          }}
          placeholder="e.g. Sitha Steels"
        />
      </div>
      <p className="mt-2 max-w-prose text-xs text-muted">
        The agent reads masters straight from Tally's local gateway — no export
        step. Tally must be running with a company open on the shop PC when a
        pull runs. Leave the name blank to use whichever company is open.
      </p>
      {err && <p className="err">{err}</p>}
    </div>
  );
}

// -------------------------------------------------------------------------
// pull block — the button + a step indicator, polled while live
// -------------------------------------------------------------------------

const PULL_STEPS = [
  "Queued",
  "Agent picked it up",
  "Exported from Tally",
  "Parsed",
  "Ready",
] as const;

function stepIndex(job: TallySyncJob): number {
  switch (job.status) {
    case "queued":
      return 0;
    case "sent":
      return 1;
    case "running":
      return 3;
    case "ok":
      return 4;
    case "error":
      return job.r2_key ? 3 : job.last_agent_status ? 2 : 1;
    default:
      return 0;
  }
}

function reachabilityBlockReason(
  agentOnline: boolean,
  tallyStatus: FirmTallyShop["tally_status"],
): string | null {
  if (!agentOnline) {
    return "The shop's agent hasn't checked in recently — it may be offline.";
  }
  if (tallyStatus && tallyStatus !== "connected") {
    return tallyStatusLabel(tallyStatus).label;
  }
  return null;
}

function PullBlock({
  firmId,
  company,
  onPulled,
  tallyStatus,
  agentOnline,
}: {
  firmId: string;
  company: TallyCompany;
  onPulled: () => void;
  tallyStatus: FirmTallyShop["tally_status"];
  agentOnline: boolean;
}) {
  const qc = useQueryClient();
  const [jobId, setJobId] = useState<string | null>(null);
  const [startErr, setStartErr] = useState<string | null>(null);

  const job = useQuery({
    queryKey: ["admin-firm-tally-job", firmId, jobId],
    queryFn: () => adminApi.getTallySyncJob(firmId, jobId as string),
    enabled: !!jobId,
    refetchInterval: (q) => {
      const s = q.state.data?.status;
      if (s === "ok" || s === "error") return false;
      return s === "running" ? 5000 : 15000;
    },
  });

  useEffect(() => {
    const s = job.data?.status;
    if (s === "ok" || s === "error") {
      onPulled();
      void qc.invalidateQueries({ queryKey: ["admin-firm-tally-jobs", firmId] });
    }
  }, [job.data?.status, firmId, onPulled, qc]);

  const start = useMutation({
    mutationFn: () => adminApi.pullTallyMasters(firmId),
    onSuccess: (j) => {
      setStartErr(null);
      setJobId(j.id);
    },
    onError: (e) =>
      setStartErr(e instanceof ApiError ? e.message : "Could not start the pull"),
  });

  const active = job.data;
  const inFlight =
    active &&
    (active.status === "queued" ||
      active.status === "sent" ||
      active.status === "running");
  const blockedReason = !inFlight
    ? reachabilityBlockReason(agentOnline, tallyStatus)
    : null;
  const disabled = start.isPending || !!inFlight || !!blockedReason;

  return (
    <div className="mt-5">
      <p className="text-xs font-semibold uppercase tracking-[0.06em] text-muted">
        Pull masters from Tally
      </p>
      <p className="mt-1 max-w-prose text-xs text-muted">
        Parties (Sundry Debtors &amp; Creditors) and stock items land in a
        review batch. Nothing is written until the firm commits it.
      </p>

      <div className="mt-3 flex flex-wrap items-center gap-3">
        <button
          className="btn-primary"
          disabled={disabled}
          onClick={() => start.mutate()}
        >
          {start.isPending ? "Starting…" : "Pull masters now"}
        </button>
        {!active && company.last_masters_pull_at && (
          <span className="text-xs text-muted">
            last pull {timeAgo(company.last_masters_pull_at)}
          </span>
        )}
      </div>

      {blockedReason && !inFlight && (
        <p className="mt-2 text-xs text-warn">{blockedReason}</p>
      )}
      {startErr && <p className="err">{startErr}</p>}
      {active && (
        <PullSteps job={active} firmId={firmId} onRetry={() => setJobId(null)} />
      )}
    </div>
  );
}

function PullSteps({
  job,
  firmId,
  onRetry,
}: {
  job: TallySyncJob;
  firmId: string;
  onRetry: () => void;
}) {
  const qc = useQueryClient();
  const idx = stepIndex(job);
  const failed = job.status === "error";
  const waiting =
    job.status === "sent" &&
    (job.last_agent_status === "no_company_loaded" ||
      job.last_agent_status === "tally_unavailable");

  const retry = useMutation({
    mutationFn: () => adminApi.retryTallySyncJob(firmId, job.id),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["admin-firm-tally-jobs", firmId] });
      onRetry();
    },
  });

  return (
    <div className="mt-3 rounded-lg border border-line bg-ground/40 p-3">
      <ol className="flex flex-wrap gap-x-2 gap-y-1 text-xs">
        {PULL_STEPS.map((label, i) => {
          const done = i < idx || job.status === "ok";
          const current = i === idx && !failed && job.status !== "ok";
          const dead = failed && i === idx;
          return (
            <li key={label} className="flex items-center gap-2">
              <span
                className={`h-1.5 w-1.5 rounded-full ${
                  dead
                    ? "bg-danger"
                    : done
                      ? "bg-ok"
                      : current
                        ? "animate-pulse bg-accent"
                        : "bg-line"
                }`}
              />
              <span
                className={
                  dead
                    ? "font-semibold text-danger"
                    : done
                      ? "text-ink"
                      : current
                        ? "font-semibold text-accent"
                        : "text-muted"
                }
              >
                {label}
              </span>
              {i < PULL_STEPS.length - 1 && <span className="text-line">›</span>}
            </li>
          );
        })}
      </ol>

      {waiting && (
        <p className="mt-2 text-xs text-warn">
          Waiting on Tally — it's closed or no company is open on the shop PC.
          Retrying each check-in; gives up after about 10 minutes.
        </p>
      )}

      {job.status === "ok" && (
        <p className="mt-2 text-xs text-ok">
          {job.counts?.ledgers ?? 0} parties + {job.counts?.items ?? 0} items
          staged. The firm reviews and commits them under{" "}
          <span className="font-semibold">Parties → Import</span> (and{" "}
          <span className="font-semibold">Items → Import</span>) in their own
          login.
        </p>
      )}

      {failed && (
        <div className="mt-2 flex flex-wrap items-center gap-3">
          <span className="text-xs text-danger">{job.error}</span>
          {job.r2_key && (
            <button
              className="text-xs text-accent hover:underline"
              onClick={() => downloadJobXml(firmId, job.id)}
            >
              Download the XML
            </button>
          )}
          <button
            className="rounded border border-line px-2 py-0.5 text-xs enabled:hover:bg-card disabled:opacity-40"
            disabled={retry.isPending}
            onClick={() => retry.mutate()}
          >
            {retry.isPending ? "Retrying…" : "Retry"}
          </button>
        </div>
      )}
    </div>
  );
}

// -------------------------------------------------------------------------
// ledger map — collapsed by default (it's really F1b's)
// -------------------------------------------------------------------------

const LEDGER_SLOTS: { key: keyof LedgerMapPatch; label: string; f1a: boolean }[] = [
  { key: "debtors_parent", label: "Debtors group", f1a: true },
  { key: "creditors_parent", label: "Creditors group", f1a: true },
  { key: "sales_ledger", label: "Sales ledger", f1a: false },
  { key: "round_off_ledger", label: "Round-off ledger", f1a: false },
  { key: "cgst_ledger", label: "CGST ledger", f1a: false },
  { key: "sgst_ledger", label: "SGST ledger", f1a: false },
  { key: "igst_ledger", label: "IGST ledger", f1a: false },
];

function LedgerMapBlock({
  firmId,
  company,
  onSaved,
}: {
  firmId: string;
  company: TallyCompany;
  onSaved: () => void;
}) {
  const [open, setOpen] = useState(false);
  const known = company.known_ledgers ?? [];
  const noPullYet = known.length === 0;

  if (!open) {
    return (
      <button
        className="mt-5 text-xs font-semibold text-accent hover:underline"
        onClick={() => setOpen(true)}
      >
        Set up invoice-push ledgers →
      </button>
    );
  }

  return (
    <div className="mt-5">
      <div className="flex items-baseline justify-between">
        <p className="text-xs font-semibold uppercase tracking-[0.06em] text-muted">
          Invoice-push ledgers
        </p>
        <button
          className="text-[11px] text-muted hover:text-ink"
          onClick={() => setOpen(false)}
        >
          hide
        </button>
      </div>
      <p className="mt-1 max-w-prose text-xs text-muted">
        Used when Metal ERP pushes sales vouchers back into Tally (next slice,
        F1b). Not used by the masters pull.
        {noPullYet && " Run a pull first to load ledger names."}
      </p>

      <div className="mt-3 flex flex-wrap gap-4">
        {LEDGER_SLOTS.map((slot) => (
          <LedgerSelect
            key={slot.key}
            firmId={firmId}
            slot={slot}
            value={company.ledger_map[slot.key] ?? ""}
            options={known}
            disabled={noPullYet}
            onSaved={onSaved}
          />
        ))}
      </div>
    </div>
  );
}

function LedgerSelect({
  firmId,
  slot,
  value,
  options,
  disabled,
  onSaved,
}: {
  firmId: string;
  slot: { key: keyof LedgerMapPatch; label: string; f1a: boolean };
  value: string;
  options: KnownLedger[];
  disabled: boolean;
  onSaved: () => void;
}) {
  const wantGroup =
    slot.key === "debtors_parent" || slot.key === "creditors_parent";
  const list = useMemo(
    () =>
      options
        .filter((o) => (wantGroup ? o.kind === "group" : o.kind === "ledger"))
        .map((o) => o.name)
        .sort((a, b) => a.localeCompare(b)),
    [options, wantGroup],
  );

  const save = useMutation({
    mutationFn: (v: string) =>
      adminApi.putTallyLedgerMap(firmId, { [slot.key]: v } as LedgerMapPatch),
    onSuccess: onSaved,
  });

  return (
    <div>
      <label className="label">
        {slot.label}
        {!slot.f1a && (
          <span className="ml-1 normal-case text-[10px] text-muted">· F1b</span>
        )}
      </label>
      <select
        className="field w-52"
        value={value}
        disabled={disabled}
        onChange={(e) => save.mutate(e.target.value)}
      >
        <option value="">— pick —</option>
        {value && !list.includes(value) && <option value={value}>{value}</option>}
        {list.map((n) => (
          <option key={n} value={n}>
            {n}
          </option>
        ))}
      </select>
    </div>
  );
}

// -------------------------------------------------------------------------
// recent pulls — last 10
// -------------------------------------------------------------------------

function RecentPulls({ firmId }: { firmId: string }) {
  const jobs = useQuery({
    queryKey: ["admin-firm-tally-jobs", firmId],
    queryFn: () => adminApi.listTallySyncJobs(firmId),
  });
  const rows = jobs.data ?? [];
  if (rows.length === 0) return null;

  return (
    <div className="mt-5">
      <p className="text-xs font-semibold uppercase tracking-[0.06em] text-muted">
        Recent pulls
      </p>
      <div className="mt-2 overflow-x-auto rounded-lg border border-line">
        <table className="w-full min-w-[520px] text-xs">
          <thead>
            <tr className="bg-ground/50 text-left text-[10px] uppercase tracking-[0.06em] text-muted">
              <th className="px-3 py-2 font-semibold">When</th>
              <th className="px-3 py-2 font-semibold">Status</th>
              <th className="px-3 py-2 font-semibold tabular-nums">Parties</th>
              <th className="px-3 py-2 font-semibold tabular-nums">Items</th>
              <th className="px-3 py-2 font-semibold">Detail</th>
              <th className="px-3 py-2" />
            </tr>
          </thead>
          <tbody>
            {rows.map((j) => (
              <tr key={j.id} className="border-t border-line">
                <td className="whitespace-nowrap px-3 py-2 text-muted">
                  {timeAgo(j.created_at)}
                </td>
                <td className="px-3 py-2">
                  <JobStatusTag job={j} />
                </td>
                <td className="px-3 py-2 tabular-nums text-muted">
                  {j.counts?.ledgers ?? "—"}
                </td>
                <td className="px-3 py-2 tabular-nums text-muted">
                  {j.counts?.items ?? "—"}
                </td>
                <td className="px-3 py-2 text-muted">
                  {j.status === "error" ? (
                    <span className="text-danger">{j.error}</span>
                  ) : j.status === "ok" ? (
                    "staged"
                  ) : (
                    j.last_agent_status?.replace(/_/g, " ") ?? "in progress"
                  )}
                </td>
                <td className="px-3 py-2 text-right">
                  {j.status === "error" && j.r2_key && (
                    <button
                      className="text-accent hover:underline"
                      onClick={() => downloadJobXml(firmId, j.id)}
                    >
                      XML
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function JobStatusTag({ job }: { job: TallySyncJob }) {
  const map: Record<TallySyncJob["status"], [string, string]> = {
    queued: ["queued", "bg-line text-muted"],
    sent: ["waiting", "bg-warn/15 text-warn"],
    running: ["running", "bg-accent/15 text-accent"],
    ok: ["ok", "bg-ok/15 text-ok"],
    error: ["failed", "bg-danger/15 text-danger"],
  };
  const [label, cls] = map[job.status];
  return (
    <span className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ${cls}`}>
      {label}
    </span>
  );
}

// -------------------------------------------------------------------------

function timeAgo(iso: string): string {
  const m = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (m < 1) return "just now";
  if (m < 60) return `${m}m ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.round(h / 24)}d ago`;
}
