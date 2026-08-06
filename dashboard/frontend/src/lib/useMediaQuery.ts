import { useSyncExternalStore } from 'react'
import { MQ_DESKTOP, MQ_MOBILE } from './breakpoints'

/**
 * Mobile-First Restructuring Phase 1 (2026-08-06) — built on
 * useSyncExternalStore deliberately: its getSnapshot is read synchronously
 * during the FIRST render, unlike a useState+useEffect implementation
 * (which would render once with a wrong/default value, then correct
 * itself after mount). That difference is what actually prevents a
 * one-frame "Sidebar renders, then swaps to MobileBottomNav" flash on
 * page load at a narrow viewport.
 */
export function useMediaQuery(query: string): boolean {
  const subscribe = (onChange: () => void) => {
    const mql = window.matchMedia(query)
    mql.addEventListener('change', onChange)
    return () => mql.removeEventListener('change', onChange)
  }
  const getSnapshot = () => window.matchMedia(query).matches
  return useSyncExternalStore(subscribe, getSnapshot)
}

/** The one binary decision App.tsx's Shell() needs: Sidebar vs MobileBottomNav. */
export function useIsDesktopNav(): boolean {
  return useMediaQuery(MQ_DESKTOP)
}

export type Breakpoint = 'mobile' | 'tablet' | 'desktop'

/** Built now (unused by Phase 1 itself) since later phases (Mission Center
 * wizard, table->card conversion) will want tablet-vs-mobile nuance and
 * it's free to add alongside the primitive above. */
export function useBreakpoint(): Breakpoint {
  const isDesktop = useMediaQuery(MQ_DESKTOP)
  const isMobile = useMediaQuery(MQ_MOBILE)
  if (isDesktop) return 'desktop'
  if (isMobile) return 'mobile'
  return 'tablet'
}
