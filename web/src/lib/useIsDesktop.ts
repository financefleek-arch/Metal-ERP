import { useEffect, useState } from "react";

/**
 * True at >= md (768px). Mobile is the primary layout; components use this
 * only to *add* the desktop two-pane enhancement, never to gate core UI.
 */
const QUERY = "(min-width: 768px)";

export function useIsDesktop(): boolean {
  const [isDesktop, setIsDesktop] = useState(
    () => typeof window !== "undefined" && window.matchMedia(QUERY).matches,
  );

  useEffect(() => {
    const mq = window.matchMedia(QUERY);
    const onChange = () => setIsDesktop(mq.matches);
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);

  return isDesktop;
}
