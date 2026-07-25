import { create } from 'zustand'
import { persist } from 'zustand/middleware'

/**
 * Shell UI-layout state only (sidebar/bottom-panel chrome) — never server
 * data. Poll results, log entries, and auth status stay exactly where they
 * are today (usePolling per module, AuthProvider context) so a later
 * React Query migration never has to touch this store.
 *
 * Persisted under its own localStorage key, separate from useHashTab.ts's
 * `iatis.activeTab`, so the two can never collide or corrupt each other.
 */

export type BottomPanelTab = 'logs' | 'console' | 'raw-json' | 'notes'

interface UiState {
  sidebarCollapsed: boolean
  expandedSections: Record<string, boolean>
  bottomPanelOpen: boolean
  bottomPanelHeight: number
  bottomPanelTab: BottomPanelTab
  setSidebarCollapsed: (v: boolean) => void
  toggleSidebarCollapsed: () => void
  toggleSection: (section: string) => void
  setBottomPanelOpen: (v: boolean) => void
  toggleBottomPanelOpen: () => void
  setBottomPanelHeight: (h: number) => void
  setBottomPanelTab: (t: BottomPanelTab) => void
}

export const BOTTOM_PANEL_DEFAULT_HEIGHT = 280
export const BOTTOM_PANEL_MIN_HEIGHT = 120
export const BOTTOM_PANEL_COLLAPSED_HEIGHT = 36

export const useUiStore = create<UiState>()(
  persist(
    (set) => ({
      sidebarCollapsed: false,
      expandedSections: {},
      bottomPanelOpen: false,
      bottomPanelHeight: BOTTOM_PANEL_DEFAULT_HEIGHT,
      bottomPanelTab: 'logs',
      setSidebarCollapsed: (v) => set({ sidebarCollapsed: v }),
      toggleSidebarCollapsed: () => set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),
      toggleSection: (section) =>
        set((s) => ({ expandedSections: { ...s.expandedSections, [section]: !(s.expandedSections[section] ?? true) } })),
      setBottomPanelOpen: (v) => set({ bottomPanelOpen: v }),
      toggleBottomPanelOpen: () => set((s) => ({ bottomPanelOpen: !s.bottomPanelOpen })),
      setBottomPanelHeight: (h) => set({ bottomPanelHeight: Math.max(BOTTOM_PANEL_MIN_HEIGHT, h) }),
      setBottomPanelTab: (t) => set({ bottomPanelTab: t }),
    }),
    { name: 'iatis.ui' }
  )
)
