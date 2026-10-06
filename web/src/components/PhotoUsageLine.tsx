import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { fileSize } from "../lib/catalog";

/** How much photo storage this firm uses. Unused photos are cleaned up automatically each day. */
export function PhotoUsageLine() {
  const u = useQuery({
    queryKey: ["media-usage"],
    queryFn: () => api<{ images: number; bytes: number }>("/media/usage"),
    staleTime: 60_000,
  });
  if (!u.data) return null;
  return (
    <p className="mt-3 text-xs text-muted">
      Photos: <span className="tabular-nums">{u.data.images.toLocaleString("en-IN")}</span> picture
      {u.data.images === 1 ? "" : "s"}, <span className="tabular-nums">{fileSize(u.data.bytes)}</span>. Pictures no
      item uses are removed automatically each day.
    </p>
  );
}
