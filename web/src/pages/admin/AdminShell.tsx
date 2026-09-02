import type { ReactNode } from "react";
import { useAuth } from "../../lib/auth";

/** Operator chrome — deliberately unlike the tenant app (dark teal bar,
 *  "· Operations" mark) so it is always obvious you are in platform mode. */
export function AdminShell({ children }: { children: ReactNode }) {
  const { me, logout } = useAuth();
  return (
    <div className="flex min-h-[100dvh] flex-col bg-ground">
      <header className="flex h-14 items-center justify-between bg-[#1a2b33] px-4 text-[#eef4f6] md:px-6">
        <span className="font-serif text-lg font-semibold">
          Metal ERP <span className="text-accent">· Operations</span>
        </span>
        <div className="flex items-center gap-4 text-sm">
          <span className="text-[#8fa9b2]">{me?.email}</span>
          <button onClick={logout} className="text-[#8fa9b2] hover:text-[#eef4f6]">
            Sign out
          </button>
        </div>
      </header>
      <main className="flex-1 px-4 py-5 lg:px-8">{children}</main>
    </div>
  );
}
