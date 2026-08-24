import { useEffect, useRef, useState } from "react";

export interface Resource<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
}

export interface ResourceOptions {
  /** When false, nothing is fetched and the last value is kept. */
  enabled?: boolean;
}

/**
 * Fetch something whenever `key` changes, aborting the request in flight.
 *
 * The previous value is kept on screen while the next one loads. Dragging a
 * price slider fires a request per change, and clearing `data` on each of them
 * would make the map blink empty between keystrokes; a stale list under a
 * loading flag reads far better than no list at all.
 *
 * `key` — not the loader — is the dependency, because the loader is a fresh
 * closure on every render and would otherwise refetch forever.
 */
export function useResource<T>(
  key: string,
  load: (signal: AbortSignal) => Promise<T>,
  { enabled = true }: ResourceOptions = {},
): Resource<T> {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(enabled);
  const [error, setError] = useState<string | null>(null);

  const loadRef = useRef(load);
  loadRef.current = load;

  useEffect(() => {
    if (!enabled) {
      setLoading(false);
      return;
    }

    const controller = new AbortController();
    setLoading(true);

    loadRef
      .current(controller.signal)
      .then((value) => {
        if (controller.signal.aborted) return;
        setData(value);
        setError(null);
      })
      .catch((cause: unknown) => {
        // An abort is this hook superseding itself, not a failure to report.
        if (controller.signal.aborted) return;
        setError(cause instanceof Error ? cause.message : "Error desconocido");
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });

    return () => controller.abort();
  }, [key, enabled]);

  return { data, loading, error };
}
