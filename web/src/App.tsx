import { Navigate, useLocation, Route, Routes } from "react-router-dom";
import { useAuth } from "./lib/auth";
import { Shell } from "./components/Shell";
import { AuthPage } from "./pages/AuthPage";
import { PublicCatalogPage } from "./pages/public/PublicCatalogPage";
import { PublicOrderPage } from "./pages/public/PublicOrderPage";
import { DeleteAccountPage } from "./pages/public/LegalPages";
import { OrdersPage } from "./pages/orders/OrdersPage";
import { FirmPage } from "./pages/FirmPage";
import { AccountPage, ForcedPasswordPage } from "./pages/AccountPage";
import { SetupPage } from "./pages/SetupPage";
import { PlansPage } from "./pages/PlansPage";
import { UpgradeProvider } from "./components/UpgradeProvider";
import { WelcomePage } from "./pages/WelcomePage";
import { FeatureRoute } from "./components/FeatureRoute";
import { PartiesPage } from "./pages/PartiesPage";
import { ItemsPage } from "./pages/ItemsPage";
import { SortGroupsPage } from "./pages/items/SortGroupsPage";
import { ImportPage } from "./pages/parties/ImportPage";
import { ItemsImportPage } from "./pages/items/ImportPage";
import { InvoiceListPage } from "./pages/invoices/InvoiceListPage";
import { InvoiceEditorPage } from "./pages/invoices/InvoiceEditorPage";
import { CollectionsPage } from "./pages/CollectionsPage";
import { InwardListPage } from "./pages/inward/InwardListPage";
import { InwardSettingsPage } from "./pages/inward/InwardSettingsPage";
import { InwardDebugPage } from "./pages/inward/InwardDebugPage";
import { CustomerCatalogsPage } from "./pages/CustomerCatalogsPage";
import { DocumentsPage } from "./pages/documents/DocumentsPage";
import { CatalogReviewPage } from "./pages/catalog/CatalogReviewPage";
import { AdminShell } from "./pages/admin/AdminShell";
import { FirmsPage } from "./pages/admin/FirmsPage";

/** The old price-list list now lives in Supplier documents (keeps ?supplier=...). */
function CatalogsRedirect() {
  const { search } = useLocation();
  return <Navigate to={`/documents${search}`} replace />;
}

function Loading() {
  return <div className="grid h-full place-items-center text-sm text-muted">Loading…</div>;
}

export function App() {
  const { me, loading } = useAuth();
  const { pathname } = useLocation();

  // The account-deletion page Google Play links to: public, no login, no shell.
  if (/^\/delete-account\/?$/.test(pathname)) {
    return (
      <Routes>
        <Route path="/delete-account" element={<DeleteAccountPage />} />
      </Routes>
    );
  }

  // A customer opening a shop's catalog link: no login, no shell.
  if (/^\/[co]\/[^/]+\/?$/.test(pathname)) {
    return (
      <Routes>
        <Route path="/c/:token" element={<PublicCatalogPage />} />
        <Route path="/o/:token" element={<PublicOrderPage />} />
      </Routes>
    );
  }

  if (loading) return <Loading />;
  if (!me) return <AuthPage />;

  // Fleek set this password: nothing else opens until the person chooses their own.
  if (me.must_change_password) return <ForcedPasswordPage />;

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
  const catalogs = me.ext_supplier_catalog;

  return (
    <UpgradeProvider>
    <Shell>
      <Routes>
        <Route path="/" element={<Navigate to="/invoices" replace />} />
        <Route path="/plans" element={<PlansPage />} />
        <Route path="/firm" element={<FirmPage />} />
        <Route path="/account" element={<AccountPage />} />
        <Route path="/setup" element={<SetupPage />} />
        <Route path="/welcome" element={<WelcomePage />} />
        <Route path="/invoices" element={<InvoiceListPage />} />
        <Route path="/invoices/new" element={<InvoiceEditorPage />} />
        <Route path="/invoices/:id" element={<InvoiceEditorPage />} />
        <Route
          path="/collections"
          element={
            <FeatureRoute feature="collections">
              <CollectionsPage />
            </FeatureRoute>
          }
        />
        <Route path="/parties" element={<PartiesPage />} />
        <Route
          path="/parties/import"
          element={
            <FeatureRoute feature="import">
              <ImportPage />
            </FeatureRoute>
          }
        />
        <Route path="/parties/new" element={<PartiesPage />} />
        <Route path="/parties/:id" element={<PartiesPage />} />
        <Route path="/items" element={<ItemsPage />} />
        <Route
          path="/items/import"
          element={
            <FeatureRoute feature="import">
              <ItemsImportPage />
            </FeatureRoute>
          }
        />
        <Route path="/items/sort-groups" element={<SortGroupsPage />} />
        {catalogs && <Route path="/items/catalogs" element={<CustomerCatalogsPage />} />}
        {catalogs && <Route path="/orders" element={<OrdersPage />} />}
        {catalogs && <Route path="/orders/:id" element={<OrdersPage />} />}
        <Route path="/items/new" element={<ItemsPage />} />
        <Route path="/items/bulk" element={<ItemsPage />} />
        <Route path="/items/categories" element={<ItemsPage />} />
        <Route path="/items/g/:groupId" element={<ItemsPage />} />
        <Route path="/items/:id" element={<ItemsPage />} />

        {/* Inward Bill Import — only when the tenant flag is on */}
        {(inward || catalogs) && <Route path="/documents" element={<DocumentsPage />} />}
        {inward && <Route path="/inward" element={<Navigate to="/documents" replace />} />}
        {inward && <Route path="/inward/settings" element={<InwardSettingsPage />} />}
        {inward && <Route path="/inward/debug" element={<InwardDebugPage />} />}
        {inward && <Route path="/inward/:id" element={<InwardListPage />} />}

        {/* Supplier Catalog — only when the tenant flag is on */}
        {catalogs && <Route path="/catalogs" element={<CatalogsRedirect />} />}
        {catalogs && <Route path="/catalogs/:id" element={<CatalogReviewPage />} />}

        <Route path="*" element={<Navigate to="/invoices" replace />} />
      </Routes>
    </Shell>
    </UpgradeProvider>
  );
}
