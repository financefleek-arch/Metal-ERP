import { Navigate, Route, Routes } from "react-router-dom";
import { useAuth } from "./lib/auth";
import { Shell } from "./components/Shell";
import { AuthPage } from "./pages/AuthPage";
import { FirmPage } from "./pages/FirmPage";
import { PartiesPage } from "./pages/PartiesPage";

function Loading() {
  return <div className="grid h-full place-items-center text-sm text-muted">Loading…</div>;
}

export function App() {
  const { me, loading } = useAuth();

  if (loading) return <Loading />;
  if (!me) return <AuthPage />;

  return (
    <Shell>
      <Routes>
        <Route path="/" element={<Navigate to="/parties" replace />} />
        <Route path="/firm" element={<FirmPage />} />
        <Route path="/parties" element={<PartiesPage />} />
        <Route path="*" element={<Navigate to="/parties" replace />} />
      </Routes>
    </Shell>
  );
}
