import { useEffect, useState } from "react";

/**
 * Debounce a fast-changing value (a search box, usually) so downstream
 * queries fire on a pause, not on every keystroke. Returns the last value
 * that has held steady for `ms`.
 */
export function useDebounced<T>(value: T, ms = 250): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const h = window.setTimeout(() => setV(value), ms);
    return () => window.clearTimeout(h);
  }, [value, ms]);
  return v;
}
