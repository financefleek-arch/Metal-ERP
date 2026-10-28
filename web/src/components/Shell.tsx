import { useEffect, useRef, useState, type ReactNode } from "react";
import { NavLink, useLocation, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { COUNTER_HIDDEN_NAV, canDraft } from "../lib/roles";
import { JobsIndicator } from "./JobsIndicator";
import { useSetup } from "../lib/setup";
import { CommandPalette, ShortcutHelp } from "./CommandPalette";

/** "g" then this letter jumps to the section (shown in each nav link's tooltip). */
const GO_KEYS: Record<string, string> = {
  "/invoices": "s",
  "/collections": "c",
  "/parties": "p",
  "/items": "i",
  "/orders": "o",
  "/documents": "d",
  "/firm": "f",
};

const baseNav = [
  { to: "/invoices", label: "Sales" },
  { to: "/collections", label: "Collections" },
  { to: "/parties", label: "Parties" },
  { to: "/items", label: "Items" },
  { to: "/firm", label: "Firm" },
];

const TAB_PATHS = ["/invoices", "/collections", "/parties", "/items"];
const TAB_LABEL: Record<string, string> = { "/invoices": "Sales", "/collections": "Collect" };

/** 22px line icons for the mobile tab bar and More sheet. */
function NavIcon({ to }: { to: string }) {
  const d: Record<string, string> = {
    "/invoices": "M7 3h8l4 4v14H7zM15 3v4h4M10 12h6M10 16h6",
    "/collections": "M6 5h12M6 9h12M9 5c5 0 6 3 3 5l-3 3 7 6M6 13h8",
    "/parties": "M12 12a4 4 0 100-8 4 4 0 000 8zM4 21c0-4 3.5-6 8-6s8 2 8 6",
    "/items": "M4 8l8-4 8 4v8l-8 4-8-4zM4 8l8 4 8-4M12 12v8",
    "/orders": "M5 4h14v16H5zM9 9h6M9 13h6",
    "/documents": "M4 6h6l2 2h8v11H4z",
    "/setup": "M5 12l4 4 10-10",
    "/firm": "M12 15a3 3 0 100-6 3 3 0 000 6zM19 12l2-1-2-4-2 1-2-1-1-2h-4l-1 2-2 1-2-1-2 4 2 1v2l-2 1 2 4 2-1 2 1 1 2h4l1-2 2-1 2 1 2-4-2-1z",
    more: "M5 12h.01M12 12h.01M19 12h.01",
  };
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={to === "more" ? 3 : 1.7} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d={d[to] ?? d["/documents"]} />
    </svg>
  );
}

export function Shell({ children }: { children: ReactNode }) {
  const { me, logout } = useAuth();
  const { pathname } = useLocation();
  const [moreOpen, setMoreOpen] = useState(false);

  // One door for a supplier's bills and price lists, when the tenant has either module.
  // new customer orders waiting for review (the badge on Orders)
  const orderCounts = useQuery({
    queryKey: ["order-counts"],
    queryFn: () => api<Record<string, number>>("/orders/counts"),
    enabled: !!me?.ext_supplier_catalog,
    refetchInterval: 30_000,
    retry: false,
  });
  const newOrders = orderCounts.data?.new ?? 0;

  // first-run steps left (the owner's "Setup" entry); gone once everything is done
  const setup = useSetup(me?.role === "owner" && !me?.is_platform_admin);
  const setupLeft = setup.data && setup.data.done < setup.data.total ? setup.data : null;

  const allNav: { to: string; label: string; badge?: number }[] = [
    ...baseNav,
    ...(me?.ext_supplier_catalog ? [{ to: "/orders", label: "Orders", badge: newOrders }] : []),
    ...(me?.ext_inward_import || me?.ext_supplier_catalog
      ? [{ to: "/documents", label: "Supplier documents" }]
      : []),
    ...(setupLeft ? [{ to: "/setup", label: `Setup ${setupLeft.done}/${setupLeft.total}` }] : []),
  ];
  // a counter works on drafts and orders; money, items and settings are not theirs to open
  const nav = me?.role === "counter" ? allNav.filter((n) => !COUNTER_HIDDEN_NAV.includes(n.to)) : allNav;

  // Mobile bottom bar: the daily screens get a tab, everything else lives under More.
  const tabNav = nav.filter((n) => TAB_PATHS.includes(n.to));
  const moreNav = nav.filter((n) => !TAB_PATHS.includes(n.to));
  const moreBadge = moreNav.reduce((t, n) => t + (n.badge ?? 0), 0);
  const moreActive = moreNav.some((n) => pathname === n.to || pathname.startsWith(n.to + "/"));

  // Close the "More" sheet whenever the route changes.
  useEffect(() => setMoreOpen(false), [pathname]);

  // Keyboard: Ctrl+K palette, N new invoice, "g" then a letter to go to a
  // section, ? for the shortcut list. Plain keys only fire when not typing
  // and no dialog is open.
  const navigate = useNavigate();
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);
  const navRef = useRef(nav);
  navRef.current = nav;
  useEffect(() => {
    let gAt = 0;
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPaletteOpen((o) => !o);
        return;
      }
      if (e.ctrlKey || e.metaKey || e.altKey || e.isComposing) return;
      const t = e.target as HTMLElement | null;
      if (t?.closest("input, textarea, select, [contenteditable='true']")) return;
      if (document.querySelector(".fixed.inset-0")) return;
      const k = e.key.toLowerCase();
      if (gAt && Date.now() - gAt < 1200) {
        gAt = 0;
        const hit = navRef.current.find((n) => GO_KEYS[n.to] === k);
        if (hit) {
          e.preventDefault();
          navigate(hit.to);
        }
        return;
      }
      if (k === "g") gAt = Date.now();
      else if (k === "n" && canDraft(me?.role)) {
        e.preventDefault();
        navigate("/invoices/new");
      } else if (e.key === "?") {
        e.preventDefault();
        setHelpOpen(true);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [navigate, me?.role]);

  return (
    <div className="flex min-h-[100dvh] flex-col">
      <header className="flex h-14 items-center justify-between bg-ink px-4 text-ground max-md:h-10 max-md:bg-ground max-md:text-muted md:px-6">
        <div className="flex items-center gap-7">
          <span className="font-serif text-lg font-semibold max-md:font-sans max-md:text-xs max-md:font-medium">Metal ERP</span>
          {/* desktop inline nav */}
          <nav className="hidden gap-5 text-sm md:flex">
            {nav.map((n) => (
              <NavLink
                key={n.to}
                to={n.to}
                title={GO_KEYS[n.to] ? `G then ${GO_KEYS[n.to].toUpperCase()}` : undefined}
                className={({ isActive }) =>
                  isActive
                    ? "border-b-2 border-accent pb-[18px] text-ground"
                    : "pb-[18px] text-[#b9b3a7] hover:text-ground"
                }
              >
                {n.label}
                {n.badge ? <span className="ml-1.5 rounded-full bg-accent px-1.5 py-0.5 text-[11px] font-semibold text-ground">{n.badge}</span> : null}
              </NavLink>
            ))}
          </nav>
        </div>

        {/* desktop account cluster */}
        <div className="hidden items-center gap-4 text-sm md:flex">
          {me?.ext_supplier_catalog && <JobsIndicator />}
          <button
            onClick={() => setPaletteOpen(true)}
            className="rounded border border-white/20 px-2 py-0.5 text-[11px] text-[#b9b3a7] hover:text-ground"
            title="Go to a page, party or invoice (Ctrl+K). Press ? for all shortcuts."
          >
            Ctrl+K
          </button>
          <button
            onClick={() => setHelpOpen(true)}
            className="text-[11px] text-[#b9b3a7] hover:text-ground"
            title="All keyboard shortcuts (?)"
          >
            ? Shortcuts
          </button>
          <NavLink to="/account" className="text-[#b9b3a7] hover:text-ground" title="My account">
            {me?.email}
          </NavLink>
          <button onClick={logout} className="text-[#b9b3a7] hover:text-ground">
            Sign out
          </button>
        </div>

      </header>

      {/* mobile: bottom tab bar + "More" sheet (desktop keeps the top nav above) */}
      {moreOpen && (
        <div className="fixed inset-0 z-40 bg-black/45 md:hidden" onClick={() => setMoreOpen(false)} />
      )}
      {moreOpen && (
        <div
          role="menu"
          className="fixed inset-x-0 bottom-0 z-50 rounded-t-2xl bg-card px-2 pb-[calc(1.25rem+env(safe-area-inset-bottom,0px))] pt-2 shadow-xl md:hidden"
        >
          <div className="mx-auto mb-2 h-1 w-9 rounded-full bg-line" />
          {moreNav.map((n) => (
            <NavLink
              key={n.to}
              to={n.to}
              className="flex h-[3.25rem] items-center gap-3.5 rounded-lg px-3 text-base hover:bg-ground"
            >
              <span className="w-6 text-center text-muted"><NavIcon to={n.to} /></span>
              {n.label}
              {n.badge ? (
                <span className="ml-auto rounded-full bg-danger px-2 text-xs font-semibold text-white">{n.badge} new</span>
              ) : null}
            </NavLink>
          ))}
          <div className="mx-3 my-1 border-t border-line" />
          <NavLink to="/account" className="flex h-[3.25rem] items-center gap-3.5 rounded-lg px-3 text-base hover:bg-ground">
            <span className="w-6 text-center text-muted">☺</span>My account
          </NavLink>
          <button onClick={() => { setMoreOpen(false); setHelpOpen(true); }} className="flex h-[3.25rem] w-full items-center gap-3.5 rounded-lg px-3 text-base hover:bg-ground">
            <span className="w-6 text-center text-muted">?</span>Shortcuts
          </button>
          <button onClick={logout} className="flex h-[3.25rem] w-full items-center gap-3.5 rounded-lg px-3 text-base hover:bg-ground">
            <span className="w-6 text-center text-muted">↪</span>Sign out
            <span className="ml-auto truncate text-xs text-muted">{me?.email}</span>
          </button>
        </div>
      )}
      <nav
        aria-label="Primary"
        className="fixed inset-x-0 bottom-0 z-30 grid border-t border-line bg-card pb-[env(safe-area-inset-bottom,0px)] md:hidden"
        style={{ gridTemplateColumns: `repeat(${tabNav.length + (moreNav.length ? 1 : 0)}, minmax(0, 1fr))` }}
      >
        {tabNav.map((n) => (
          <NavLink
            key={n.to}
            to={n.to}
            onClick={() => setMoreOpen(false)}
            className={({ isActive }) =>
              `relative flex min-h-[3.25rem] flex-col items-center justify-center gap-0.5 text-[11px] font-medium ${
                isActive ? "text-accent" : "text-muted"
              }`
            }
          >
            {({ isActive }) => (
              <>
                {isActive && <span className="absolute top-0 h-[3px] w-7 rounded-b bg-accent" />}
                <NavIcon to={n.to} />
                {TAB_LABEL[n.to] ?? n.label}
              </>
            )}
          </NavLink>
        ))}
        {moreNav.length > 0 && (
          <button
            onClick={() => setMoreOpen((o) => !o)}
            aria-expanded={moreOpen}
            className={`relative flex min-h-[3.25rem] flex-col items-center justify-center gap-0.5 text-[11px] font-medium ${
              moreOpen || moreActive ? "text-accent" : "text-muted"
            }`}
          >
            <NavIcon to="more" />
            More
            {moreBadge > 0 && (
              <span className="absolute left-[calc(50%+6px)] top-1 min-w-4 rounded-full bg-danger px-1 text-center text-[11px] font-semibold text-white">
                {moreBadge}
              </span>
            )}
          </button>
        )}
      </nav>

      <main className="flex-1 px-4 pb-24 pt-4 md:pb-4 lg:px-8 lg:py-7">{children}</main>

      {paletteOpen && (
        <CommandPalette
          pages={nav}
          showParties={nav.some((n) => n.to === "/parties")}
          onClose={() => setPaletteOpen(false)}
        />
      )}
      {helpOpen && <ShortcutHelp onClose={() => setHelpOpen(false)} />}
    </div>
  );
}
