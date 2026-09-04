import { Navigate, Route, Routes } from "react-router-dom";
import { useAuth } from "./lib/auth";
import { Shell } from "./components/Shell";
import { AuthPage } from "./pages/AuthPage";
import { FirmPage } from "./pages/FirmPage";
import { PartiesPage } from "./pages/PartiesPage";
import { ItemsPage } from "./pages/ItemsPage";
import { ImportPage } from "./pages/parties/ImportPage";
import { ItemsImportPage } from "./pages/items/ImportPage";
import { InvoiceListPage } from "./pages/invoices/InvoiceListPage";
import { InvoiceEditorPage } from "./pages/invoices/InvoiceEditorPage";
import { CollectionsPage } from "./pages/CollectionsPage";
import { InwardListPage } from "./pages/inward/InwardListPage";
import { InwardSettingsPage } from "./pages/inward/InwardSettingsPage";
import { InwardDebugPage } from "./pages/inward/InwardDebugPage";
import { AdminShell } from "./pages/admin/AdminShell";
import { FirmsPage } from "./pages/admin/FirmsPage";

function Loading() {
  return <div className="grid h-full place-items-center text-sm text-muted">Loading…</div>;
}

export function App() {
  const { me, loading } = useAuth();

  if (loading) return <Loading />;
  if (!me) return <AuthPage />;

  // Platform operator: the whole app IS the admin console. A firm user who
  // somehow reaches /admin/* falls through to the normal app below.
  if (me.is_platform_admin) {
    return (
      <AdminShell>
        <Routes>
          <Route path="/admin" element={<FirmsPage />} />
          <Route path="*" element={<Navigate to="/admin" replace />} />
        </Routes>
      </AdminShell>
    );
  }

  const inward = me.ext_inward_import;

  return (
    <Shell>
      <Routes>
        <Route path="/" element={<Navigate to="/invoices" replace />} />
        <Route path="/firm" element={<FirmPage />} />
        <Route path="/invoices" element={<InvoiceListPage />} />
        <Route path="/invoices/new" element={<InvoiceEditorPage />} />
        <Route path="/invoices/:id" element={<InvoiceEditorPage />} />
        <Route path="/collections" element={<CollectionsPage />} />
        <Route path="/parties" element={<PartiesPage />} />
        <Route path="/parties/import" element={<ImportPage />} />
        <Route path="/parties/new" element={<PartiesPage />} />
        <Route path="/parties/:id" element={<PartiesPage />} />
        <Route path="/items" element={<ItemsPage />} />
        <Route path="/items/import" element={<ItemsImportPage />} />
        <Route path="/items/new" element={<ItemsPage />} />
        <Route path="/items/bulk" element={<ItemsPage />} />
        <Route path="/items/categories" element={<ItemsPage />} />
        <Route path="/items/g/:groupId" element={<ItemsPage />} />
        <Route path="/items/:id" element={<ItemsPage />} />

        {/* Inward Bill Import — only when the tenant flag is on */}
        {inward && <Route path="/inward" element={<InwardListPage />} />}
        {inward && <Route path="/inward/settings" element={<InwardSettingsPage />} />}
        {inward && <Route path="/inward/debug" element={<InwardDebugPage />} />}
        {inward && <Route path="/inward/:id" element={<InwardListPage />} />}

        <Route path="*" element={<Navigate to="/invoices" replace />} />
      </Routes>
    </Shell>
  );
}
