import type { ReactNode } from "react";

/** Shared page header: serif title, optional subtitle, actions on the right. */
export function PageHeader({
  title,
  subtitle,
  actions,
  className = "",
}: {
  title: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  className?: string;
}) {
  return (
    <div className={`mb-4 flex flex-wrap items-start justify-between gap-x-4 gap-y-2 ${className}`}>
      <div className="min-w-0">
        <h1 className="page-title">{title}</h1>
        {subtitle && <p className="mt-0.5 text-sm text-muted">{subtitle}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}
