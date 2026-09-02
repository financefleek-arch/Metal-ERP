import { api } from "../../lib/api";
import type {
  AdminUser,
  AssignableRole,
  FirmDetail,
  FirmListItem,
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
};
