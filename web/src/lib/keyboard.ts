import { useEffect, useRef, type KeyboardEvent as ReactKeyboardEvent } from "react";

/**
 * Focus a keyboard-entry field on an invoice line (`data-ln` = row key,
 * `data-f` = field name). The editor renders a mobile and a desktop copy of
 * some fields, so pick the one that is actually visible. Desktop only: on a
 * phone a programmatic focus would pop the soft keyboard unasked.
 */
export function focusField(rowKey: string, field: string) {
  if (typeof window === "undefined" || !window.matchMedia("(min-width: 768px)").matches) return;
  // after React has committed the state change that created / enabled the field
  setTimeout(() => {
    const els = document.querySelectorAll<HTMLElement>(
      `[data-ln="${rowKey}"][data-f="${field}"]`,
    );
    const el = Array.from(els).find((e) => e.offsetParent !== null);
    if (!el) return;
    el.focus();
    if (el instanceof HTMLInputElement) el.select();
  }, 0);
}

/**
 * Enter moves to the next field of a form (Tally's rhythm) instead of
 * submitting early. On the last field Enter falls through (a <form> submits),
 * and Ctrl+Enter always falls through. Put it on the form / container's
 * onKeyDown; fields that handle Enter themselves (preventDefault) are left alone.
 */
export function enterAdvance(e: ReactKeyboardEvent<HTMLElement>) {
  if (e.key !== "Enter" || e.defaultPrevented || e.ctrlKey || e.metaKey || e.shiftKey) return;
  const t = e.target as HTMLElement;
  const typeable =
    (t instanceof HTMLInputElement && !["checkbox", "radio", "button", "submit", "file"].includes(t.type)) ||
    t instanceof HTMLSelectElement;
  if (!typeable) return;
  const fields = Array.from(
    e.currentTarget.querySelectorAll<HTMLElement>("input, select, textarea"),
  ).filter(
    (el) =>
      !(el as HTMLInputElement).disabled &&
      !(el as HTMLInputElement).readOnly &&
      (el as HTMLInputElement).type !== "hidden" &&
      (el as HTMLInputElement).type !== "file" &&
      el.tabIndex >= 0 &&
      el.offsetParent !== null,
  );
  const next = fields[fields.indexOf(t) + 1];
  if (!next) return;
  e.preventDefault();
  next.focus();
  if (next instanceof HTMLInputElement) next.select();
}

/** Focus any element by id (e.g. the Finalize button), if it is enabled. */
export function focusId(id: string): boolean {
  const el = document.getElementById(id) as HTMLButtonElement | null;
  if (!el || el.disabled) return false;
  el.focus();
  return true;
}

/** The topmost full-screen overlay (the last `.fixed.inset-0` in the DOM). */
function topOverlay(): HTMLElement | null {
  const all = document.querySelectorAll<HTMLElement>(".fixed.inset-0");
  return all.length ? all[all.length - 1] : null;
}

/** Keep Tab / Shift+Tab inside the topmost dialog, wrapping at the ends. */
function trapTab(e: KeyboardEvent) {
  const root = topOverlay();
  if (!root) return;
  const items = Array.from(
    root.querySelectorAll<HTMLElement>(
      'a[href], button, input, select, textarea, [tabindex]:not([tabindex="-1"])',
    ),
  ).filter((el) => !(el as HTMLButtonElement).disabled && el.tabIndex >= 0 && el.offsetParent !== null);
  if (!items.length) return;
  const first = items[0];
  const last = items[items.length - 1];
  const active = document.activeElement as HTMLElement | null;
  if (!active || !root.contains(active)) {
    e.preventDefault();
    first.focus();
  } else if (e.shiftKey && active === first) {
    e.preventDefault();
    last.focus();
  } else if (!e.shiftKey && active === last) {
    e.preventDefault();
    first.focus();
  }
}

/**
 * Esc closes an overlay, and focus goes back to whatever opened it. Tab stays
 * inside the dialog. With
 * `onEnter`, Enter runs the primary action — but not while a button, link,
 * select or textarea has focus (Enter must still press that button).
 */
export function useDialogKeys(onClose: () => void, onEnter?: (() => void) | "primary") {
  const latest = useRef({ onClose, onEnter });
  latest.current = { onClose, onEnter };
  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    return () => opener?.focus?.();
  }, []);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        // the topmost dialog only: a nested dialog's own Esc already ran first
        if (!e.defaultPrevented) latest.current.onClose();
      } else if (e.key === "Tab") {
        trapTab(e);
      } else if (e.key === "Enter" && latest.current.onEnter && !e.isComposing) {
        const t = e.target as HTMLElement | null;
        if (e.defaultPrevented || t?.closest("button, a, select, textarea")) return;
        const act = latest.current.onEnter;
        if (act === "primary") {
          const btn = topOverlay()?.querySelector<HTMLButtonElement>(".btn-primary:not([disabled])");
          if (!btn) return;
          e.preventDefault();
          btn.click();
        } else {
          e.preventDefault();
          act();
        }
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
}
