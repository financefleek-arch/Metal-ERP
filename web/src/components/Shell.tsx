import { useEffect, useState, type ReactNode } from "react";
import { NavLink, useLocation } from "react-router-dom";
import { useAuth } from "../lib/auth";

const baseNav = [
  { to: "/invoices", label: "Sales" },
  { to: "/collections", label: "Collections" },
  { to: "/parties", label: "Parties" },
  { to: "/items", label: "Items" },
  { to: "/firm", label: "Firm" },
];

export function Shell({ children }: { children: ReactNode }) {
  const { me, logout } = useAuth();
  const { pathname } = useLocation();
  const [drawerOpen, setDrawerOpen] = useState(false);

  // Inward appears only when the tenant has ext_inward_import.
  const nav = me?.ext_inward_import
    ? [...baseNav, { to: "/inward", label: "Inward" }]
    : baseNav;

  // Close the mobile drawer whenever the route changes.
  useEffect(() => setDrawerOpen(false), [pathname]);

  return (
    <div className="flex min-h-[100dvh] flex-col">
      <header className="flex h-14 items-center justify-between bg-ink px-4 text-ground md:px-6">
        <div className="flex items-center gap-7">
          <span className="font-serif text-lg font-semibold">Metal ERP</span>
          {/* desktop inline nav */}
          <nav className="hidden gap-5 text-sm md:flex">
            {nav.map((n) => (
              <NavLink
                key={n.to}
                to={n.to}
                className={({ isActive }) =>
                  isActive
                    ? "border-b-2 border-accent pb-[18px] text-ground"
                    : "pb-[18px] text-[#b9b3a7] hover:text-ground"
                }
              >
                {n.label}
              </NavLink>
            ))}
          </nav>
        </div>

        {/* desktop account cluster */}
        <div className="hidden items-center gap-4 text-sm md:flex">
          <span className="text-[#b9b3a7]">{me?.email}</span>
          <button onClick={logout} className="text-[#b9b3a7] hover:text-ground">
            Sign out
          </button>
        </div>

        {/* mobile hamburger */}
        <button
          className="-mr-2 flex h-11 w-11 items-center justify-center text-2xl md:hidden"
          aria-label="Menu"
          aria-expanded={drawerOpen}
          onClick={() => setDrawerOpen((o) => !o)}
        >
          {drawerOpen ? "✕" : "☰"}
        </button>
      </header>

      {/* mobile drawer + backdrop */}
      {drawerOpen && (
        <div
          className="fixed inset-0 z-40 bg-black/40 md:hidden"
          onClick={() => setDrawerOpen(false)}
        />
      )}
      <nav
        className={`fixed right-0 top-0 z-50 flex h-[100dvh] w-64 max-w-[80vw] flex-col bg-ink text-ground transition-transform duration-200 md:hidden ${
          drawerOpen ? "translate-x-0" : "translate-x-full"
        }`}
      >
        <div className="flex h-14 items-center justify-between px-4">
          <span className="font-serif text-lg font-semibold">Menu</span>
          <button
            className="flex h-11 w-11 items-center justify-center text-2xl"
            aria-label="Close menu"
            onClick={() => setDrawerOpen(false)}
          >
            {"✕"}
          </button>
        </div>
        <div className="flex flex-col">
          {nav.map((n) => (
            <NavLink
              key={n.to}
              to={n.to}
              className={({ isActive }) =>
                `border-l-4 px-4 py-3 text-base ${
                  isActive
                    ? "border-accent bg-white/5 text-ground"
                    : "border-transparent text-[#b9b3a7]"
                }`
              }
            >
              {n.label}
            </NavLink>
          ))}
        </div>
        <div className="mt-auto border-t border-white/10 px-4 py-4 text-sm">
          <div className="truncate text-[#b9b3a7]">{me?.email}</div>
          <button
            onClick={logout}
            className="mt-2 text-[#b9b3a7] hover:text-ground"
          >
            Sign out
          </button>
        </div>
      </nav>

      <main className="flex-1 px-4 py-4 lg:px-8 lg:py-7">{children}</main>
    </div>
  );
}
