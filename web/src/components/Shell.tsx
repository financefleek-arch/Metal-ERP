import type { ReactNode } from "react";
import { NavLink } from "react-router-dom";
import { useAuth } from "../lib/auth";

const baseNav = [
  { to: "/parties", label: "Parties" },
  { to: "/items", label: "Items" },
  { to: "/firm", label: "Firm" },
];

export function Shell({ children }: { children: ReactNode }) {
  const { me, logout } = useAuth();
  // Inward appears only when the tenant has ext_inward_import.
  const nav = me?.ext_inward_import
    ? [...baseNav, { to: "/inward", label: "Inward" }]
    : baseNav;

  return (
    <div className="flex min-h-full flex-col">
      <header className="flex h-14 items-center justify-between bg-ink px-6 text-ground">
        <div className="flex items-center gap-7">
          <span className="font-serif text-lg font-semibold">Metal ERP</span>
          <nav className="flex gap-5 text-sm">
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
        <div className="flex items-center gap-4 text-sm">
          <span className="text-[#b9b3a7]">{me?.email}</span>
          <button onClick={logout} className="text-[#b9b3a7] hover:text-ground">
            Sign out
          </button>
        </div>
      </header>
      <main className="flex-1 px-8 py-7">{children}</main>
    </div>
  );
}
