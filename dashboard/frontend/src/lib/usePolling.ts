import { useEffect, useRef, useState, useCallback } from 'react'
import { ApiError } from './api'

interface PollingState<T> {
  data: T | null
  error: Error | null
  loading: boolean
  /** Wall-clock time of the last successful fetch, or null before the first. */
  lastUpdated: Date | null
  refetch: () => void
}

/**
 * Fetches immediately, then re-fetches every `intervalMs`.
 *
 * Visibility-aware: while the browser tab is hidden the interval is torn
 * down, and a single catch-up fetch fires the moment it becomes visible
 * again. This matters here specifically — the system's own Mission Control
 * flags API credits as a scarce, budgeted resource, and 26 always-on
 * pollers hammering providers against a backgrounded tab burned that budget
 * for readouts nobody was looking at. Stops on unmount.
 */
export function usePolling<T>(fetchFn: () => Promise<T>, intervalMs: number, onAuthError?: () => void): PollingState<T> {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<Error | null>(null)
  const [loading, setLoading] = useState(true)
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null)
  const fetchRef = useRef(fetchFn)
  fetchRef.current = fetchFn

  const run = useCallback(async () => {
    try {
      const result = await fetchRef.current()
      setData(result)
      setError(null)
      setLastUpdated(new Date())
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        onAuthError?.()
        return
      }
      setError(err instanceof Error ? err : new Error(String(err)))
    } finally {
      setLoading(false)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [onAuthError])

  useEffect(() => {
    let timer: ReturnType<typeof setInterval> | undefined

    const start = () => {
      if (timer !== undefined) return
      timer = setInterval(run, intervalMs)
    }
    const stop = () => {
      if (timer === undefined) return
      clearInterval(timer)
      timer = undefined
    }
    const onVisibility = () => {
      if (document.hidden) {
        stop()
      } else {
        run() // catch up on whatever was missed while hidden
        start()
      }
    }

    run()
    if (!document.hidden) start()
    document.addEventListener('visibilitychange', onVisibility)

    return () => {
      stop()
      document.removeEventListener('visibilitychange', onVisibility)
    }
  }, [run, intervalMs])

  return { data, error, loading, lastUpdated, refetch: run }
}
