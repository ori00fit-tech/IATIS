/**
 * Mobile-First Restructuring Phase 1 (2026-08-06) — the single source of
 * truth for viewport tiers, matching the operator's own bucketing:
 * mobile <640px / tablet 640-1023px / desktop >=1024px. 1024px is also the
 * Sidebar<->MobileBottomNav swap point (see lib/useMediaQuery.ts).
 */
export const BP_MOBILE_MAX = 639
export const BP_TABLET_MIN = 640
export const BP_TABLET_MAX = 1023
export const BP_DESKTOP_MIN = 1024

export const MQ_MOBILE = `(max-width: ${BP_MOBILE_MAX}px)`
export const MQ_TABLET = `(min-width: ${BP_TABLET_MIN}px) and (max-width: ${BP_TABLET_MAX}px)`
export const MQ_DESKTOP = `(min-width: ${BP_DESKTOP_MIN}px)`
