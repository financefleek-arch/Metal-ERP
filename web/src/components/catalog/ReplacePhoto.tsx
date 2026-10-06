import { useRef, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { apiUpload, ApiError } from "../../lib/api";

/** Use another picture for a price-list row (the PDF's crop was wrong or missing). */
export function ReplacePhoto({
  catalogId,
  rowId,
  hasPhoto,
  onDone,
}: {
  catalogId: string;
  rowId: string;
  hasPhoto: boolean;
  onDone: () => void;
}) {
  const ref = useRef<HTMLInputElement>(null);
  const [err, setErr] = useState<string | null>(null);
  const up = useMutation({
    mutationFn: (file: File) => {
      const fd = new FormData();
      fd.append("file", file);
      return apiUpload(`/supplier-catalogs/${catalogId}/items/${rowId}/photo`, fd);
    },
    onSuccess: () => {
      setErr(null);
      onDone();
    },
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Could not use that photo."),
  });
  return (
    <div className="mt-0.5 w-[102px] text-center">
      <input
        ref={ref}
        type="file"
        accept="image/*"
        className="sr-only"
        tabIndex={-1}
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) up.mutate(f);
          e.target.value = "";
        }}
      />
      <button
        type="button"
        className="text-[11px] text-accent hover:underline disabled:opacity-50"
        disabled={up.isPending}
        onClick={() => ref.current?.click()}
      >
        {up.isPending ? "Saving…" : hasPhoto ? "Replace photo" : "Add photo"}
      </button>
      {err && <p className="text-[10px] text-danger">{err}</p>}
    </div>
  );
}
