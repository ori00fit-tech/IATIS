import { QueryClient } from '@tanstack/react-query'

/**
 * Singleton query cache (Phase 2 institutional-redesign). Scoped for now to
 * whatever calls useApiQuery — only Backtesting Lab/Charts do. Every other
 * module stays on usePolling untouched.
 *
 * retry: false matches usePolling's own zero-retry behavior — a failed tick
 * just waits for the next interval instead of hammering a struggling
 * endpoint. refetchIntervalInBackground: false + the default
 * refetchOnWindowFocus keep the same "don't poll a hidden tab, catch up on
 * focus" budget-conservation goal usePolling implements by hand.
 */
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: false,
      refetchOnWindowFocus: true,
      refetchIntervalInBackground: false,
      staleTime: 0,
    },
  },
})
