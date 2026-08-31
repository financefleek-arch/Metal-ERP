import { useQuery } from "@tanstack/react-query";
import { api } from "./api";

export interface StateOption {
  code: string;
  name: string;
}

export function useStates() {
  return useQuery({
    queryKey: ["reference", "states"],
    queryFn: () => api<StateOption[]>("/reference/states", { auth: true }),
    staleTime: Infinity,
    gcTime: Infinity,
  });
}

// Client-side format hints — the backend is the real guarantee (422).
export const PAN_RE = /^[A-Z]{5}[0-9]{4}[A-Z]$/;
export const GSTIN_RE = /^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$/;

export function panError(v: string): string | undefined {
  if (!v) return undefined;
  return PAN_RE.test(v.toUpperCase()) ? undefined : "PAN must look like AAAAA9999A";
}

export function gstinError(v: string): string | undefined {
  if (!v) return undefined;
  return GSTIN_RE.test(v.toUpperCase()) ? undefined : "GSTIN must be 15 chars: 99AAAAA9999A9Z9";
}
