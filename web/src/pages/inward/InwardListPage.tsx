import { useRef, useState } from "react";
import { NavLink, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError, getToken } from "../../lib/api";
import { useIsDesktop } from "../../lib/useIsDesktop";
import type { InwardBillListItem, InwardStatus } from "../../lib/inward";
import { InwardReviewPane } from "./InwardReviewPane";

const STATUS_LABEL: Record<InwardStatus, string> = {
  uploaded: "Uploaded",
  extracting: "Extracting",
  needs_review: "Needs review",
  approved: "Approved",
  rejected: "Rejected",
  error: "Error",
};

const STATUS_CLASS: Record<InwardStatus, string> = {
  uploaded: "bg-line text-muted",
  extracting: "bg-accent-soft text-accent",
  needs_review: "bg-[#f1e7d6] text-warn",
  approved: "bg-[#e6efe8] text-ok",
  rejected: "bg-[#f4e3df] text-danger",
  error: "bg-[#f4e3df] text-danger",
};

function StatusChip({ s }: { s: InwardStatus }) {
  return (
    <span className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${STATUS_CLASS[s]}`}>
      {STATUS_LABEL[s]}
    </span>
  );
}

export function InwardListPage() {
  const { id: selectedId } = useParams();
  const nav = useNavigate();
  const qc = useQueryClient();
  const fileRef = useRef<HTMLInputElement>(null);
  const [dragOver, setDragOver] = useState(false);
  const [uploadErr, setUploadErr] = useState<string | null>(null);
  const isDesktop = useIsDesktop();
  const showRailPane = isDesktop || !selectedId;
  const showDetailPane = isDesktop || !!selectedId;

  const list = useQuery({
    queryKey: ["inward-bills"],
    queryFn: () => api<InwardBillListItem[]>("/inward-bills"),
    refetchInterval: (q) =>
      (q.state.data ?? []).some((b) => b.status === "extracting") ? 2000 : false,
  });

  const upload = useMutation({
    mutationFn: async (files: FileList) => {
      const fd = new FormData();
      Array.from(files).forEach((f) => fd.append("files", f));
      const res = await fetch("/api/inward-bills", {
        method: "POST",
        headers: { Authorization: `Bearer ${getToken()}` },
        body: fd,
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new ApiError(res.status, body.detail ?? res.statusText);
      }
      return res.json();
    },
    onSuccess: () => {
      setUploadErr(null);
      void qc.invalidateQueries({ queryKey: ["inward-bills"] });
    },
    onError: (e: unknown) =>
      setUploadErr(e instanceof ApiError ? e.message : "Upload failed"),
  });

  function onFiles(files: FileList | null) {
    if (files && files.length) upload.mutate(files);
  }

  return (
    <div className="mx-auto flex min-h-[calc(100dvh-6.5rem)] max-w-6xl flex-col rounded-xl border border-line bg-card md:h-full md:min-h-0 md:flex-row md:overflow-hidden">
      {/* rail */}
      <div
        className={`${
          showRailPane ? "flex" : "hidden"
        } w-full shrink-0 flex-col border-b border-line bg-ground md:flex md:w-[340px] md:border-b-0 md:border-r`}
      >
        <div className="border-b border-line p-3">
          <div className="mb-2 flex items-center justify-between">
            <span className="text-sm font-semibold">Inward bills</span>
            <button
              className="btn-primary h-7 px-3 text-xs"
              onClick={() => fileRef.current?.click()}
              disabled={upload.isPending}
            >
              ↑ Upload PDF
            </button>
          </div>
          <div
            className={`cursor-pointer rounded-lg border border-dashed p-4 text-center text-xs transition-colors ${
              dragOver ? "border-accent bg-accent-soft" : "border-accent/60 text-accent"
            }`}
            onClick={() => fileRef.current?.click()}
            onDragOver={(e) => {
              e.preventDefault();
              setDragOver(true);
            }}
            onDragLeave={() => setDragOver(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDragOver(false);
              onFiles(e.dataTransfer.files);
            }}
          >
            {upload.isPending
              ? "Uploading…"
              : isDesktop
                ? "Drop supplier PDFs, or click to choose"
                : "Tap to choose supplier PDFs"}
            <span className="mt-1 block text-[10px] text-muted">
              PDF only · 20 MB max · one file extracts now
            </span>
          </div>
          {uploadErr && <p className="err">{uploadErr}</p>}
          <input
            ref={fileRef}
            type="file"
            accept="application/pdf"
            multiple
            className="hidden"
            onChange={(e) => onFiles(e.target.files)}
          />
        </div>

        <div className="flex-1 overflow-y-auto">
          {list.isLoading && <p className="p-4 text-xs text-muted">Loading…</p>}
          {list.data?.length === 0 && (
            <p className="p-4 text-xs text-muted">No bills yet. Upload a supplier PDF.</p>
          )}
          {list.data?.map((b) => (
            <NavLink
              key={b.id}
              to={`/inward/${b.id}`}
              className={({ isActive }) =>
                `block border-b border-line px-3 py-3 text-xs hover:bg-accent-soft/60 md:py-2.5 ${
                  isActive ? "bg-accent-soft" : ""
                }`
              }
            >
              <div className="flex items-center justify-between gap-2">
                <span className="truncate font-medium">
                  {b.supplier_name ?? b.source_filename}
                </span>
                <StatusChip s={b.status} />
              </div>
              <div className="mt-0.5 flex items-center justify-between text-muted">
                <span className="font-mono">
                  {b.bill_no ?? "—"}
                  {b.bill_date ? ` · ${b.bill_date}` : ""}
                </span>
                <span className="font-mono">{b.grand_total ?? ""}</span>
              </div>
            </NavLink>
          ))}
        </div>
      </div>

      {/* detail */}
      <div
        className={`${
          showDetailPane ? "flex" : "hidden"
        } min-w-0 flex-1 flex-col md:flex`}
      >
        {!isDesktop && selectedId && (
          <button
            className="flex items-center gap-2 border-b border-line px-4 py-3 text-sm font-medium text-accent md:hidden"
            onClick={() => nav("/inward")}
          >
            ← Inward bills
          </button>
        )}
        <div className="min-w-0 flex-1 overflow-y-auto">
          {selectedId ? (
            <InwardReviewPane billId={selectedId} />
          ) : (
            <div className="grid h-full place-items-center text-sm text-muted">
              Select a bill, or upload a supplier PDF.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
