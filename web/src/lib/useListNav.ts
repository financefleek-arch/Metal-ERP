import { useEffect, useRef, useState } from "react";

/** Highlight for the keyboard-active row. */
export const NAV_ACTIVE = "bg-accent-soft shadow-[inset_3px_0_0_theme(colors.accent.DEFAULT)]";

const TYPING = "input, textarea, select, [contenteditable='true']";

/**
 * Keyboard navigation for a list page, in the Tally manner:
 *   /        focus the search box (id `list-search`)
 *   ↓ (in search)  jump into the list
 *   ↑ ↓      move the highlighted row
 *   Enter    open the highlighted row
 * One list per page. Give each row `{...navProps(i)}` and `isActive(i)`.
 * Inert while a dialog is open (any `.fixed.inset-0` overlay) so a dialog's
 * own keys win.
 */
export function useListNav<T>(items: T[], onOpen: (item: T) => void, searchId = "list-search") {
  const [active, setActive] = useState(-1);
  const latest = useRef({ items, onOpen, active });
  latest.current = { items, onOpen, active };

  // the list shrank (new search / filter): never point past the end
  useEffect(() => {
    setActive((a) => (a >= items.length ? items.length - 1 : a));
  }, [items.length]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.ctrlKey || e.metaKey || e.altKey || e.isComposing) return;
      if (document.querySelector(".fixed.inset-0")) return;
      const { items: list, onOpen: open, active: cur } = latest.current;
      const t = e.target as HTMLElement | null;
      const typing = !!t?.closest(TYPING);

      if (typing) {
        if (t?.id === searchId && e.key === "ArrowDown" && list.length) {
          e.preventDefault();
          (t as HTMLInputElement).blur();
          setActive(0);
        }
        return;
      }
      if (e.key === "/") {
        const el = document.getElementById(searchId) as HTMLInputElement | null;
        if (el) {
          e.preventDefault();
          el.focus();
          el.select();
        }
      } else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        if (!list.length) return;
        e.preventDefault();
        // so Enter goes to the list, not to a button that happens to have focus
        (document.activeElement as HTMLElement | null)?.blur?.();
        setActive(
          e.key === "ArrowDown"
            ? Math.min(cur + 1, list.length - 1)
            : Math.max(cur - 1, 0),
        );
      } else if (e.key === "Enter" && cur >= 0 && list[cur] !== undefined) {
        if (t?.closest("button, a")) return; // Enter presses the focused control
        e.preventDefault();
        open(list[cur]);
      } else if (e.key === "Escape" && cur >= 0) {
        setActive(-1);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [searchId]);

  useEffect(() => {
    if (active >= 0) {
      document.querySelector(`[data-nav="${active}"]`)?.scrollIntoView({ block: "nearest" });
    }
  }, [active]);

  return {
    active,
    isActive: (i: number) => i === active,
    navProps: (i: number) => ({ "data-nav": i }),
    searchId,
  };
}
