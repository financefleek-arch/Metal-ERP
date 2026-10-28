import { useEffect, useRef } from "react";

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

/** Focus any element by id (e.g. the Finalize button), if it is enabled. */
export function focusId(id: string): boolean {
  const el = document.getElementById(id) as HTMLButtonElement | null;
  if (!el || el.disabled) return false;
  el.focus();
  return true;
}

/**
 * Esc closes an overlay, and focus goes back to whatever opened it. With
 * `onEnter`, Enter runs the primary action — but not while a button, link,
 * select or textarea has focus (Enter must still press that button).
 */
export function useDialogKeys(onClose: () => void, onEnter?: () => void) {
  const latest = useRef({ onClose, onEnter });
  latest.current = { onClose, onEnter };
  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    return () => opener?.focus?.();
  }, []);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        latest.current.onClose();
      } else if (e.key === "Enter" && latest.current.onEnter && !e.isComposing) {
        const t = e.target as HTMLElement | null;
        if (e.defaultPrevented || t?.closest("button, a, select, textarea")) return;
        e.preventDefault();
        latest.current.onEnter();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
}
