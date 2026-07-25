import { useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ApiError } from './api'

interface ApiQueryState<T> {
  data: T | null
  error: Error | null
  loading: boolean
  lastUpdated: Date | null
  refetch: () => void
}

/**
 * React Query-backed replacement for usePolling.ts, matching its exact
 * external contract — scoped to Backtesting Lab/Charts only (Phase 2
 * institutional redesign); every other module's usePolling caller is
 * completely unaffected, this hook and usePolling coexist indefinitely.
 *
 * Behavioral parity, verified against @tanstack/react-query's actual
 * types/docs rather than assumed:
 *  - loading -> query.isLoading, which RQ v5 defines as isPending &&
 *    isFetching: true only until the very first fetch settles (success or
 *    error), never true again on a background refetch tick. This matches
 *    usePolling's own "Loading… shows once, never flickers again" property
 *    (its `loading` state is flipped false in a `finally` after the first
 *    run and same-value re-sets on every later tick never trigger a
 *    re-render). Using isFetching instead would flicker every interval.
 *  - visibility-awareness: refetchIntervalInBackground:false (queryClient.ts)
 *    doesn't tear the interval down while hidden, it just no-ops the
 *    refetch each tick — zero network calls fire either way, and
 *    refetchOnWindowFocus (default true) fires an immediate catch-up
 *    refetch on the same visibility/focus events usePolling listens to by
 *    hand. Functionally equivalent budget-conservation, verified live in
 *    the Phase 2 verification pass (hide-tab / confirm-zero-calls / refocus
 *    / confirm-catch-up).
 *  - 401s: retry:false (queryClient.ts) means a 401 settles on the first
 *    attempt; intercepted here and routed to onAuthError instead of ever
 *    reaching the returned `error` field, mirroring usePolling's own
 *    `if (err instanceof ApiError && err.status === 401) { onAuthError?.();
 *    return }` (which skips setError).
 *  - lastUpdated derives from React Query's own dataUpdatedAt (updated only
 *    on a resolved queryFn, never on error) rather than a parallel
 *    useState, so it can't drift from React Query's own source of truth.
 */
export function useApiQuery<T>(
  queryKey: readonly unknown[],
  fetchFn: () => Promise<T>,
  intervalMs: number,
  onAuthError?: () => void
): ApiQueryState<T> {
  const query = useQuery<T>({ queryKey, queryFn: fetchFn, refetchInterval: intervalMs })

  const is401 = query.error instanceof ApiError && query.error.status === 401
  useEffect(() => {
    if (is401) onAuthError?.()
  }, [is401, onAuthError])

  return {
    data: query.data ?? null,
    error: is401 ? null : (query.error as Error | null),
    loading: query.isLoading,
    lastUpdated: query.dataUpdatedAt ? new Date(query.dataUpdatedAt) : null,
    refetch: () => {
      void query.refetch()
    },
  }
}
