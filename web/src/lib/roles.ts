import type { UserRole } from "./types";

/** Owner and accountant do everything: finalize, payments, reminders, Tally, settings. */
export const canWrite = (role?: UserRole): boolean => role === "owner" || role === "accountant";

/** A counter also prepares drafts, customers and orders. A viewer can only look. */
export const canDraft = (role?: UserRole): boolean => canWrite(role) || role === "counter";

/** Sidebar entries a counter does not get (the server refuses the actions behind them anyway). */
export const COUNTER_HIDDEN_NAV = ["/collections", "/items", "/firm"];
