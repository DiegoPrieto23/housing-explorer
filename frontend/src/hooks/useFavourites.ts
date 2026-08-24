import { useCallback, useEffect, useState } from "react";

/**
 * Favourites, kept in the browser and nowhere else.
 *
 * No account, no backend: a favourite is a `fuente:id` in `localStorage`. That
 * is the whole feature, and it is the right size for it — the alternative is
 * asking someone to create an account to remember four flats.
 *
 * What that costs, and what the UI should therefore not promise: favourites do
 * not follow the user to another browser, another device, or a private window,
 * and clearing site data loses them.
 */

const KEY = "housing-explorer:favoritos";

/** Fired on ourselves, because the native `storage` event only reaches *other* tabs. */
const CHANGED = "housing-explorer:favoritos-cambiados";

function read(): Set<string> {
  try {
    const raw = window.localStorage.getItem(KEY);
    if (!raw) return new Set();
    const parsed: unknown = JSON.parse(raw);
    // The value is user-editable and survives deploys, so it is treated as
    // untrusted input rather than as something we wrote: anything that is not a
    // list of strings is discarded instead of crashing the app on load.
    return Array.isArray(parsed)
      ? new Set(parsed.filter((item): item is string => typeof item === "string"))
      : new Set();
  } catch {
    // Private browsing, or storage disabled entirely. Favourites simply stop
    // persisting; nothing else should break.
    return new Set();
  }
}

function write(ids: Set<string>): void {
  try {
    window.localStorage.setItem(KEY, JSON.stringify([...ids]));
  } catch {
    // Quota exceeded or storage blocked: the in-memory set still works for
    // this session, which is better than an error the user cannot act on.
  }
}

export interface Favourites {
  ids: Set<string>;
  count: number;
  has: (globalId: string) => boolean;
  toggle: (globalId: string) => void;
  clear: () => void;
}

export function useFavourites(): Favourites {
  const [ids, setIds] = useState<Set<string>>(read);

  // Two listeners for two different things: `storage` fires when *another* tab
  // writes, and the custom event when another hook instance in *this* tab does.
  // Without the second, the sidebar count and the card's star would drift apart.
  useEffect(() => {
    const refresh = () => setIds(read());
    window.addEventListener("storage", refresh);
    window.addEventListener(CHANGED, refresh);
    return () => {
      window.removeEventListener("storage", refresh);
      window.removeEventListener(CHANGED, refresh);
    };
  }, []);

  const toggle = useCallback((globalId: string) => {
    setIds((current) => {
      const next = new Set(current);
      if (!next.delete(globalId)) next.add(globalId);
      write(next);
      window.dispatchEvent(new Event(CHANGED));
      return next;
    });
  }, []);

  const clear = useCallback(() => {
    setIds(() => {
      const next = new Set<string>();
      write(next);
      window.dispatchEvent(new Event(CHANGED));
      return next;
    });
  }, []);

  const has = useCallback((globalId: string) => ids.has(globalId), [ids]);

  return { ids, count: ids.size, has, toggle, clear };
}
