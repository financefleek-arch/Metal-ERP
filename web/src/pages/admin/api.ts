import { api } from "../../lib/api";
import { downloadFile } from "../../lib/download";
import type {
  AdminUser,
  AssignableRole,
  FirmDetail,
  FirmListItem,
  FirmTallyShop,
  FirmTallyShopProvisionResult,
  FirmWhatsapp,
  FirmWhatsappTestResult,
  FirmWhatsappUpsert,
  LedgerMapPatch,
  TallyCompany,
  TallyCompanyUpsert,
  TallySyncJob,
} from "../../lib/types";

export const adminApi = {
  listFirms: (q?: string) =>
    api<FirmListItem[]>(`/admin/firms${q ? `?q=${encodeURIComponent(q)}` : ""}`),

  getFirm: (id: string) => api<FirmDetail>(`/admin/firms/${id}`),

  createFirm: (body: { legal_name: string; city?: string | null }) =>
    api<FirmDetail>("/admin/firms", { method: "POST", body }),

  patchFirm: (
    id: string,
    body: Partial<{
      legal_name: string;
      city: string | null;
      gst_enabled: boolean;
      ext_inward_import: boolean;
    }>,
  ) => api<FirmDetail>(`/admin/firms/${id}`, { method: "PATCH", body }),

  createUser: (
    firmId: string,
    body: { email: string; password: string; role: AssignableRole },
  ) => api<AdminUser>(`/admin/firms/${firmId}/users`, { method: "POST", body }),

  patchUser: (
    userId: string,
    body: Partial<{ role: AssignableRole; is_active: boolean; password: string }>,
  ) => api<AdminUser>(`/admin/users/${userId}`, { method: "PATCH", body }),

  disableUser: (userId: string) =>
    api<void>(`/admin/users/${userId}`, { method: "DELETE" }),

  getFirmWhatsapp: (firmId: string) =>
    api<FirmWhatsapp>(`/admin/firms/${firmId}/whatsapp`),

  putFirmWhatsapp: (firmId: string, body: FirmWhatsappUpsert) =>
    api<FirmWhatsapp>(`/admin/firms/${firmId}/whatsapp`, { method: "PUT", body }),

  deleteFirmWhatsapp: (firmId: string) =>
    api<void>(`/admin/firms/${firmId}/whatsapp`, { method: "DELETE" }),

  testFirmWhatsapp: (
    firmId: string,
    body: { to_phone: string; template_name?: string; with_document?: boolean },
  ) =>
    api<FirmWhatsappTestResult>(`/admin/firms/${firmId}/whatsapp/test`, {
      method: "POST",
      body,
    }),

  // ---- companion agent (per firm) ----
  // Provisioning IS the installer: the backend mints the key, bakes it into
  // a zip, and caches it — nothing here ever sees the plaintext key.

  getFirmAgent: (firmId: string) =>
    api<FirmTallyShop>(`/admin/firms/${firmId}/tally-shop`),

  provisionFirmAgent: (firmId: string) =>
    api<FirmTallyShopProvisionResult>(`/admin/firms/${firmId}/tally-shop`, {
      method: "POST",
    }),

  rotateFirmAgentKey: (firmId: string) =>
    api<FirmTallyShopProvisionResult>(`/admin/firms/${firmId}/tally-shop/rotate-key`, {
      method: "POST",
    }),

  downloadFirmAgentInstaller: (firmId: string, filename: string) =>
    downloadFile(`/admin/firms/${firmId}/tally-shop/installer`, filename),

  // ---- Tally Connector (F1a) ----

  getTallyCompany: (firmId: string) =>
    api<TallyCompany>(`/admin/firms/${firmId}/tally/company`),

  upsertTallyCompany: (firmId: string, body: TallyCompanyUpsert) =>
    api<TallyCompany>(`/admin/firms/${firmId}/tally/company`, {
      method: "POST",
      body,
    }),

  putTallyLedgerMap: (firmId: string, body: LedgerMapPatch) =>
    api<TallyCompany>(`/admin/firms/${firmId}/tally/company/ledger-map`, {
      method: "PUT",
      body,
    }),

  pullTallyMasters: (firmId: string) =>
    api<TallySyncJob>(`/admin/firms/${firmId}/tally/pull-masters`, {
      method: "POST",
    }),

  listTallySyncJobs: (firmId: string) =>
    api<TallySyncJob[]>(`/admin/firms/${firmId}/tally/sync-jobs`),

  getTallySyncJob: (firmId: string, jobId: string) =>
    api<TallySyncJob>(`/admin/firms/${firmId}/tally/sync-jobs/${jobId}`),

  retryTallySyncJob: (firmId: string, jobId: string) =>
    api<TallySyncJob>(
      `/admin/firms/${firmId}/tally/sync-jobs/${jobId}/retry`,
      { method: "POST" },
    ),
};
