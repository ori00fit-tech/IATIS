import { useEffect, useRef, useState, useCallback } from 'react'
import { ApiError } from './api'

interface PollingState<T> {
  data: T | null
  error: Error | null
  loading: boolean
  refetch: () => void
}

/** Fetches immediately, then re-fetches every `intervalMs`. Stops on unmount. */
export function usePolling<T>(fetchFn: () => Promise<T>, intervalMs: number, onAuthError?: () => void): PollingState<T> {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<Error | null>(null)
  const [loading, setLoading] = useState(true)
  const fetchRef = useRef(fetchFn)
  fetchRef.current = fetchFn

  const run = useCallback(async () => {
    try {
      const result = await fetchRef.current()
      setData(result)
      setError(null)
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
    run()
    const id = setInterval(run, intervalMs)
    return () => clearInterval(id)
  }, [run, intervalMs])

  return { data, error, loading, refetch: run }
}
