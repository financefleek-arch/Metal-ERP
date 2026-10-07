import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthProvider } from "./lib/auth";
import { App } from "./App";
import "./index.css";

const qc = new QueryClient({
  // Refresh on return to the tab (stale after 20s) so a second tab or a message that landed while
  // away shows up without a page reload. Long infinite lists opt out individually.
  defaultOptions: { queries: { retry: 1, staleTime: 20_000, refetchOnWindowFocus: true } },
});

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={qc}>
      <BrowserRouter>
        <AuthProvider>
          <App />
        </AuthProvider>
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>,
);
