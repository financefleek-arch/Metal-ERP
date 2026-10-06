import { useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, ApiError, apiUpload } from "../lib/api";
import { ImageLightbox } from "./catalog/ImageLightbox";
import type { Item } from "../lib/types";

// iOS only converts HEIC to JPEG when the accept list names the formats we take.
const ACCEPT = "image/jpeg,image/png,image/webp";
const MAX_MB = 10;

interface PhotoOut {
  photo_url: string | null;
  thumb_url: string | null;
}

/** The item's photo on the item page: view large, add or replace (camera or file), remove. */
export function ItemPhoto({ item, onChanged }: { item: Item; onChanged: () => void }) {
  const qc = useQueryClient();
  const fileRef = useRef<HTMLInputElement>(null);
  const cameraRef = useRef<HTMLInputElement>(null);
  const [err, setErr] = useState<string | null>(null);
  const [zoom, setZoom] = useState(false);

  const done = () => {
    qc.invalidateQueries({ queryKey: ["items"] });
    onChanged();
  };
  const upload = useMutation({
    mutationFn: (file: File) => {
      const fd = new FormData();
      fd.append("file", file);
      return apiUpload<PhotoOut>(`/items/${item.id}/photo`, fd);
    },
    onSuccess: () => {
      setErr(null);
      done();
    },
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Could not add the photo. Try again."),
  });
  const remove = useMutation({
    mutationFn: () => api(`/items/${item.id}/photo`, { method: "DELETE" }),
    onSuccess: () => {
      setErr(null);
      done();
    },
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Could not remove the photo."),
  });

  function pick(files: FileList | null) {
    setErr(null);
    const f = files?.[0];
    if (!f) return;
    if (f.size > MAX_MB * 1024 * 1024) {
      setErr(`That photo is over ${MAX_MB} MB.`);
      return;
    }
    upload.mutate(f);
  }

  const has = !!item.photo_url;
  return (
    <div className="flex items-start gap-3">
      {has ? (
        <button
          type="button"
          aria-label={`View photo of ${item.name}`}
          className="shrink-0 cursor-zoom-in"
          onClick={() => setZoom(true)}
        >
          <img
            src={item.thumb_url ?? item.photo_url!}
            alt={item.name}
            className="h-20 w-20 rounded-md border border-line object-cover"
          />
        </button>
      ) : (
        <div
          className="grid h-20 w-20 shrink-0 place-items-center rounded-md border border-dashed border-line text-center text-[10px] text-muted"
          aria-label="No photo"
        >
          No photo
        </div>
      )}
      <div className="min-w-0 text-xs">
        <input
          ref={fileRef}
          type="file"
          accept={ACCEPT}
          className="sr-only"
          aria-label="Choose a photo file"
          onChange={(e) => {
            pick(e.target.files);
            e.target.value = "";
          }}
        />
        <input
          ref={cameraRef}
          type="file"
          accept={ACCEPT}
          capture="environment"
          className="sr-only"
          aria-label="Take a photo"
          onChange={(e) => {
            pick(e.target.files);
            e.target.value = "";
          }}
        />
        <div className="flex flex-wrap gap-1.5">
          <button
            type="button"
            className="btn-ghost h-8 px-2.5 text-xs"
            disabled={upload.isPending}
            onClick={() => cameraRef.current?.click()}
          >
            {upload.isPending ? "Adding…" : "Take photo"}
          </button>
          <button
            type="button"
            className="btn-ghost h-8 px-2.5 text-xs"
            disabled={upload.isPending}
            onClick={() => fileRef.current?.click()}
          >
            {has ? "Replace" : "Choose photo"}
          </button>
          {has && (
            <button
              type="button"
              className="h-8 px-2 text-xs text-danger hover:underline"
              disabled={remove.isPending}
              onClick={() => remove.mutate()}
            >
              Remove
            </button>
          )}
        </div>
        {err && (
          <p className="err mt-1" role="alert">
            {err}
          </p>
        )}
      </div>
      {zoom && item.photo_url && (
        <ImageLightbox
          src={item.photo_url}
          title={item.name}
          caption={item.sku ? `Code ${item.sku}` : undefined}
          onClose={() => setZoom(false)}
        />
      )}
    </div>
  );
}
