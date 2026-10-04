import { useEffect, useState } from "react";

/** Full-size view of a catalog photo. Click the photo to zoom in and out; Esc or a click outside closes. */
export function ImageLightbox({
  src,
  title,
  caption,
  onClose,
}: {
  src: string;
  title: string;
  caption?: string;
  onClose: () => void;
}) {
  const [zoom, setZoom] = useState(false);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-[60] flex flex-col bg-black/80"
      role="dialog"
      aria-modal="true"
      aria-label={title}
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}
    >
      <div className="flex items-center justify-between gap-3 p-3 text-white">
        <div className="min-w-0">
          <p className="truncate font-medium">{title}</p>
          {caption && <p className="truncate text-xs text-white/70">{caption}</p>}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <button type="button" className="rounded-md bg-white/15 px-3 py-1.5 text-sm hover:bg-white/25" onClick={() => setZoom((z) => !z)}>
            {zoom ? "Fit to screen" : "Zoom in"}
          </button>
          <button type="button" className="rounded-md bg-white/15 px-3 py-1.5 text-sm hover:bg-white/25" onClick={onClose}>
            Close
          </button>
        </div>
      </div>
      <div
        className={`min-h-0 flex-1 p-3 ${zoom ? "overflow-auto" : "grid place-items-center overflow-hidden"}`}
        onMouseDown={(e) => e.target === e.currentTarget && onClose()}
      >
        <img
          src={src}
          alt={title}
          onClick={() => setZoom((z) => !z)}
          className={zoom ? "mx-auto max-w-none cursor-zoom-out" : "max-h-full max-w-full cursor-zoom-in object-contain"}
          style={zoom ? { width: "min(220%, 2400px)" } : undefined}
        />
      </div>
    </div>
  );
}
