"use client";

import { useCallback, useEffect, useState } from "react";
import { ApiError } from "./api";

export interface AsyncState<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
  /** Re-runs the request, e.g. after a mutation. */
  refresh: () => void;
}

interface Settled<T> {
  /** Which request produced this - see `loading` below. */
  key: string;
  data: T | null;
  error: string | null;
}

/**
 * Loads data from the API and tracks loading/error state.
 *
 * Every page carried its own copy of this effect, and every copy had the
 * same bug: `error` was set on failure but never cleared on a later
 * success, so once the error box appeared it stayed for the rest of the
 * session even after the data loaded fine.
 *
 * `loading` is *derived* rather than stored: it is true whenever the
 * settled result belongs to a different request than the current one. That
 * keeps the effect free of synchronous setState (which React 19 flags as a
 * cascading render) and makes "deps changed, result is stale" the same
 * state as "first load", which is what a caller wants anyway.
 *
 * `deps` must be primitives - they are serialised to identify a request.
 * `fetcher` should be wrapped in useCallback over those same deps.
 */
export function useApi<T>(fetcher: () => Promise<T>, deps: unknown[] = []): AsyncState<T> {
  const [reloadToken, setReloadToken] = useState(0);
  const [settled, setSettled] = useState<Settled<T>>({
    key: "",
    data: null,
    error: null,
  });

  const key = `${reloadToken}:${JSON.stringify(deps)}`;
  const refresh = useCallback(() => setReloadToken((n) => n + 1), []);

  useEffect(() => {
    // Guards against a slow earlier request resolving after a newer one
    // and overwriting fresher data (or a stale error).
    let current = true;

    fetcher()
      .then((data) => {
        if (current) setSettled({ key, data, error: null });
      })
      .catch((cause: unknown) => {
        if (current) setSettled({ key, data: null, error: errorMessage(cause) });
      });

    return () => {
      current = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  return {
    data: settled.key === key ? settled.data : null,
    error: settled.key === key ? settled.error : null,
    loading: settled.key !== key,
    refresh,
  };
}

/** Normalises anything thrown by the api client into a message. */
export function errorMessage(cause: unknown): string {
  if (cause instanceof ApiError || cause instanceof Error) return cause.message;
  return "Unbekannter Fehler.";
}
