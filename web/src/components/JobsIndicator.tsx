import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";

interface ActiveJob {
  kind: string;
  title: string;
  detail: string | null;
  progress: number | null;
  total: number | null;
  link: string | null;
}

/** A small "working" pill in the header: what is running in the background, in one place. */
export function JobsIndicator() {
  const [open, setOpen] = useState(false);
  const jobs = useQuery({
    queryKey: ["jobs-active"],
    queryFn: () => api<ActiveJob[]>("/jobs/active"),
    refetchInterval: (q) => ((q.state.data?.length ?? 0) > 0 ? 3000 : 15000),
    refetchIntervalInBackground: false,
    retry: false,
  });
  const list = jobs.data ?? [];
  if (list.length === 0) return null;
  return (
    <div className="relative">
      <button
        type="button"
        className="flex items-center gap-2 rounded-full bg-accent px-3 py-1 text-xs font-medium text-ground"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
      >
        <span aria-hidden className="inline-block h-2 w-2 animate-pulse rounded-full bg-ground" />
        {list.length === 1 ? list[0].title : `${list.length} things running`}
      </button>
      {open && (
        <ul
          role="status"
          className="absolute right-0 z-30 mt-2 w-72 divide-y divide-line rounded-lg border border-line bg-card p-1 text-xs text-ink shadow-xl"
        >
          {list.map((j, i) => (
            <li key={i} className="px-3 py-2">
              <p className="font-medium">{j.title}</p>
              <p className="text-muted tabular-nums">
                {j.total ? `${(j.progress ?? 0).toLocaleString("en-IN")} of ${j.total.toLocaleString("en-IN")}` : "working"}
                {j.detail ? ` · ${j.detail}` : ""}
              </p>
              {j.link && (
                <Link to={j.link} className="text-accent underline" onClick={() => setOpen(false)}>
                  Open
                </Link>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
