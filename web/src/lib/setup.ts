import { useQuery } from "@tanstack/react-query";
import { api } from "./api";
import type { SetupOverview } from "./types";

export const SETUP_KEY = ["setup"] as const;

/** The Setup checklist and feature cards (GET /api/onboarding). `enabled` false for people who never see it. */
export function useSetup(enabled = true) {
  return useQuery({
    queryKey: SETUP_KEY,
    queryFn: () => api<SetupOverview>("/onboarding"),
    enabled,
    staleTime: 60_000,
    retry: false,
  });
}
