import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError } from "../../lib/api";
import type { AdminUser, AssignableRole, FirmDetail } from "../../lib/types";
import { adminApi } from "./api";

const ROLES: AssignableRole[] = ["accountant", "owner", "viewer"];

export function FirmsPage() {
  const qc = useQueryClient();
  const [search, setSearch] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  const firms = useQuery({
    queryKey: ["admin-firms", search],
    queryFn: () => adminApi.listFirms(search || undefined),
  });

  const selected =
    selectedId ?? (firms.data && firms.data.length ? firms.data[0].id : null);

  return (
    <div className="mx-auto max-w-[1060px]">
      <h1 className="mb-4 font-serif text-2xl font-semibold">Client firms</h1>
      <div className="grid gap-0 md:grid-cols-[280px_1fr] md:overflow-hidden md:rounded-xl md:border md:border-line">
        {/* left — firm list */}
        <aside className="border-line bg-ground/60 p-3 md:border-r">
          <input
            className="field"
            placeholder="Search firms…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          <ul className="mt-3 space-y-0.5">
            {firms.data?.map((f) => {
              const active = f.id === selected;
              return (
                <li key={f.id}>
                  <button
                    onClick={() => setSelectedId(f.id)}
                    className={`flex w-full items-center justify-between rounded-md px-3 py-2 text-left text-sm ${
                      active ? "bg-accent text-white" : "hover:bg-card"
                    }`}
                  >
                    <span className="truncate">{f.legal_name}</span>
                    <span
                      className={`ml-2 shrink-0 text-xs ${
                        active ? "text-white/70" : "text-muted"
                      }`}
                    >
                      {f.city ?? "—"} · {f.active_user_count}/{f.user_count}
                    </span>
                  </button>
                </li>
              );
            })}
            {firms.data?.length === 0 && (
              <li className="px-3 py-6 text-center text-sm text-muted">
                No firms match.
              </li>
            )}
          </ul>
          <button
            className="btn-primary mt-3 w-full"
            onClick={() => setCreating(true)}
          >
            + New firm
          </button>
        </aside>

        {/* right — firm detail */}
        <section className="bg-card p-4 md:p-6">
          {creating ? (
            <NewFirmForm
              onDone={(firm) => {
                setCreating(false);
                setSelectedId(firm.id);
                void qc.invalidateQueries({ queryKey: ["admin-firms"] });
              }}
              onCancel={() => setCreating(false)}
            />
          ) : selected ? (
            <FirmDetailPane key={selected} firmId={selected} />
          ) : (
            <p className="text-sm text-muted">
              Select a firm, or create one.
            </p>
          )}
        </section>
      </div>
    </div>
  );
}

// --------------------------------------------------------------------------

function NewFirmForm({
  onDone,
  onCancel,
}: {
  onDone: (f: FirmDetail) => void;
  onCancel: () => void;
}) {
  const [legalName, setLegalName] = useState("");
  const [city, setCity] = useState("");
  const [err, setErr] = useState<string | null>(null);

  const create = useMutation({
    mutationFn: () =>
      adminApi.createFirm({ legal_name: legalName.trim(), city: city.trim() || null }),
    onSuccess: onDone,
    onError: (e) =>
      setErr(e instanceof ApiError ? e.message : "Could not create firm"),
  });

  return (
    <div className="max-w-md">
      <h2 className="mb-4 font-serif text-lg font-semibold">New firm</h2>
      <label className="label">Legal name</label>
      <input
        className="field"
        autoFocus
        value={legalName}
        onChange={(e) => setLegalName(e.target.value)}
      />
      <label className="label mt-3">City</label>
      <input
        className="field"
        value={city}
        onChange={(e) => setCity(e.target.value)}
      />
      {err && <p className="err">{err}</p>}
      <div className="mt-4 flex gap-2">
        <button
          className="btn-primary"
          disabled={!legalName.trim() || create.isPending}
          onClick={() => {
            setErr(null);
            create.mutate();
          }}
        >
          {create.isPending ? "Creating…" : "Create firm"}
        </button>
        <button className="btn-ghost" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </div>
  );
}

// --------------------------------------------------------------------------

function FirmDetailPane({ firmId }: { firmId: string }) {
  const qc = useQueryClient();
  const firm = useQuery({
    queryKey: ["admin-firm", firmId],
    queryFn: () => adminApi.getFirm(firmId),
  });

  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ["admin-firm", firmId] });
    void qc.invalidateQueries({ queryKey: ["admin-firms"] });
  };

  if (firm.isLoading) return <p className="text-sm text-muted">Loading…</p>;
  if (firm.isError || !firm.data)
    return <p className="err">Could not load this firm.</p>;

  return (
    <div>
      <FirmFields firm={firm.data} onSaved={refresh} />
      <UsersTable firm={firm.data} onChanged={refresh} />
    </div>
  );
}

function FirmFields({
  firm,
  onSaved,
}: {
  firm: FirmDetail;
  onSaved: () => void;
}) {
  const [legalName, setLegalName] = useState(firm.legal_name);
  const [city, setCity] = useState(firm.city ?? "");
  const [gst, setGst] = useState(firm.gst_enabled);
  const [inward, setInward] = useState(firm.ext_inward_import);
  const [err, setErr] = useState<string | null>(null);

  const dirty =
    legalName.trim() !== firm.legal_name ||
    city.trim() !== (firm.city ?? "") ||
    gst !== firm.gst_enabled ||
    inward !== firm.ext_inward_import;

  const save = useMutation({
    mutationFn: () =>
      adminApi.patchFirm(firm.id, {
        legal_name: legalName.trim(),
        city: city.trim() || null,
        gst_enabled: gst,
        ext_inward_import: inward,
      }),
    onSuccess: onSaved,
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Save failed"),
  });

  return (
    <div className="border-b border-line pb-5">
      <div className="flex items-baseline justify-between">
        <h2 className="font-serif text-lg font-semibold">{firm.legal_name}</h2>
        <span className="text-xs text-muted">
          tenant {firm.id.slice(0, 8)} · created{" "}
          {new Date(firm.created_at).toISOString().slice(0, 10)}
        </span>
      </div>
      <div className="mt-3 flex flex-wrap gap-4">
        <div>
          <label className="label">Legal name</label>
          <input
            className="field w-64"
            value={legalName}
            onChange={(e) => setLegalName(e.target.value)}
          />
        </div>
        <div>
          <label className="label">City</label>
          <input
            className="field w-48"
            value={city}
            onChange={(e) => setCity(e.target.value)}
          />
        </div>
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-6">
        <Toggle label="GST enabled" on={gst} onToggle={() => setGst((v) => !v)} />
        <Toggle
          label="Inward Bill Import"
          on={inward}
          onToggle={() => setInward((v) => !v)}
        />
        <button
          className="btn-ghost ml-auto"
          disabled={!dirty || save.isPending}
          onClick={() => {
            setErr(null);
            save.mutate();
          }}
        >
          {save.isPending ? "Saving…" : "Save firm"}
        </button>
      </div>
      {err && <p className="err">{err}</p>}
    </div>
  );
}

function Toggle({
  label,
  on,
  onToggle,
}: {
  label: string;
  on: boolean;
  onToggle: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onToggle}
      className="flex items-center gap-2 text-sm"
    >
      <span
        className={`relative h-5 w-9 shrink-0 rounded-full transition-colors ${
          on ? "bg-accent" : "bg-line"
        }`}
      >
        <span
          className={`absolute top-0.5 h-4 w-4 rounded-full bg-white transition-all ${
            on ? "left-4" : "left-0.5"
          }`}
        />
      </span>
      {label}
    </button>
  );
}

// --------------------------------------------------------------------------

function UsersTable({
  firm,
  onChanged,
}: {
  firm: FirmDetail;
  onChanged: () => void;
}) {
  const [adding, setAdding] = useState(false);
  const [resetFor, setResetFor] = useState<string | null>(null);

  const activeOwners = useMemo(
    () => firm.users.filter((u) => u.is_active && u.role === "owner").length,
    [firm.users],
  );

  return (
    <div className="mt-5">
      <h3 className="mb-2 text-xs font-semibold uppercase tracking-[0.06em] text-muted">
        Users
      </h3>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-line text-left text-[11px] uppercase tracking-[0.05em] text-muted">
              <th className="py-2 pr-3 font-semibold">Email</th>
              <th className="py-2 pr-3 font-semibold">Role</th>
              <th className="py-2 pr-3 font-semibold">Status</th>
              <th className="py-2 text-right font-semibold">Actions</th>
            </tr>
          </thead>
          <tbody>
            {firm.users.map((u) => (
              <UserRow
                key={u.id}
                user={u}
                lastActiveOwner={
                  u.is_active && u.role === "owner" && activeOwners === 1
                }
                resetting={resetFor === u.id}
                onResetToggle={() =>
                  setResetFor((cur) => (cur === u.id ? null : u.id))
                }
                onChanged={() => {
                  setResetFor(null);
                  onChanged();
                }}
              />
            ))}
            {firm.users.length === 0 && (
              <tr>
                <td colSpan={4} className="py-6 text-center text-muted">
                  No users yet. Add one below.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {adding ? (
        <AddUserForm
          firmId={firm.id}
          onDone={() => {
            setAdding(false);
            onChanged();
          }}
          onCancel={() => setAdding(false)}
        />
      ) : (
        <button className="btn-ghost mt-3" onClick={() => setAdding(true)}>
          + Add user
        </button>
      )}
    </div>
  );
}

function UserRow({
  user,
  lastActiveOwner,
  resetting,
  onResetToggle,
  onChanged,
}: {
  user: AdminUser;
  lastActiveOwner: boolean;
  resetting: boolean;
  onResetToggle: () => void;
  onChanged: () => void;
}) {
  const [pw, setPw] = useState("");
  const [err, setErr] = useState<string | null>(null);

  const patch = useMutation({
    mutationFn: (body: Partial<{ is_active: boolean; password: string }>) =>
      adminApi.patchUser(user.id, body),
    onSuccess: () => {
      setPw("");
      onChanged();
    },
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Update failed"),
  });

  const disable = useMutation({
    mutationFn: () => adminApi.disableUser(user.id),
    onSuccess: onChanged,
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Disable failed"),
  });

  return (
    <>
      <tr className="border-b border-line/60">
        <td className="py-2 pr-3 font-mono text-xs">{user.email}</td>
        <td className="py-2 pr-3">
          <span
            className={`rounded-full px-2 py-0.5 text-[11px] font-semibold ${
              user.role === "owner"
                ? "bg-warn/15 text-warn"
                : "bg-accent-soft text-accent"
            }`}
          >
            {user.role}
          </span>
        </td>
        <td className="py-2 pr-3">
          {user.is_active ? (
            <span className="rounded-full bg-ok/15 px-2 py-0.5 text-[11px] font-semibold text-ok">
              active
            </span>
          ) : (
            <span className="rounded-full bg-line px-2 py-0.5 text-[11px] font-semibold text-muted">
              disabled
            </span>
          )}
        </td>
        <td className="py-2 text-right whitespace-nowrap">
          <button
            className="rounded border border-line px-2 py-1 text-xs text-ink hover:bg-ground"
            onClick={onResetToggle}
          >
            {resetting ? "Cancel" : "Reset password"}
          </button>{" "}
          {user.is_active ? (
            <button
              className="rounded border border-danger/40 px-2 py-1 text-xs text-danger enabled:hover:bg-danger/5 disabled:opacity-40"
              disabled={lastActiveOwner || disable.isPending}
              title={
                lastActiveOwner ? "The firm's only active owner" : undefined
              }
              onClick={() => {
                setErr(null);
                disable.mutate();
              }}
            >
              Disable
            </button>
          ) : (
            <button
              className="rounded border border-line px-2 py-1 text-xs text-ink hover:bg-ground disabled:opacity-40"
              disabled={patch.isPending}
              onClick={() => {
                setErr(null);
                patch.mutate({ is_active: true });
              }}
            >
              Enable
            </button>
          )}
        </td>
      </tr>
      {resetting && (
        <tr>
          <td colSpan={4} className="py-2">
            <div className="flex flex-wrap items-end gap-2 rounded-md bg-ground/60 p-3">
              <div>
                <label className="label">New password (you type it)</label>
                <input
                  className="field w-64"
                  type="text"
                  autoFocus
                  value={pw}
                  onChange={(e) => setPw(e.target.value)}
                  placeholder="min 8 characters"
                />
              </div>
              <button
                className="btn-primary"
                disabled={pw.length < 8 || patch.isPending}
                onClick={() => {
                  setErr(null);
                  patch.mutate({ password: pw });
                }}
              >
                {patch.isPending ? "Setting…" : "Set password"}
              </button>
              <p className="w-full text-xs text-muted">
                Relay this to the client yourself — no email is sent.
              </p>
            </div>
          </td>
        </tr>
      )}
      {err && (
        <tr>
          <td colSpan={4}>
            <p className="err">{err}</p>
          </td>
        </tr>
      )}
    </>
  );
}

function AddUserForm({
  firmId,
  onDone,
  onCancel,
}: {
  firmId: string;
  onDone: () => void;
  onCancel: () => void;
}) {
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<AssignableRole>("accountant");
  const [pw, setPw] = useState("");
  const [err, setErr] = useState<string | null>(null);

  const create = useMutation({
    mutationFn: () =>
      adminApi.createUser(firmId, {
        email: email.trim(),
        password: pw,
        role,
      }),
    onSuccess: onDone,
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Could not add user"),
  });

  return (
    <div className="mt-3 rounded-md border border-line bg-ground/40 p-4">
      <div className="mb-1 text-sm font-semibold">Add user</div>
      <div className="flex flex-wrap items-end gap-3">
        <div>
          <label className="label">Email</label>
          <input
            className="field w-64"
            autoFocus
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="name@firm.com"
          />
        </div>
        <div>
          <label className="label">Role</label>
          <select
            className="field w-40"
            value={role}
            onChange={(e) => setRole(e.target.value as AssignableRole)}
          >
            {ROLES.map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className="label">Password (you type it)</label>
          <input
            className="field w-56"
            type="text"
            value={pw}
            onChange={(e) => setPw(e.target.value)}
            placeholder="min 8 characters"
          />
        </div>
        <button
          className="btn-primary"
          disabled={!email.trim() || pw.length < 8 || create.isPending}
          onClick={() => {
            setErr(null);
            create.mutate();
          }}
        >
          {create.isPending ? "Adding…" : "Add user"}
        </button>
        <button className="btn-ghost" onClick={onCancel}>
          Cancel
        </button>
      </div>
      {err && <p className="err">{err}</p>}
      <p className="mt-2 text-xs text-muted">
        You choose the password and pass it to the client. No email is sent.
      </p>
    </div>
  );
}
