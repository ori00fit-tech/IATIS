import { TABS, type TabId } from '../../lib/tabs'

/**
 * Mobile-First Restructuring Phase 1 (2026-08-06) — fixed bottom nav bar
 * shown below the desktop breakpoint (App.tsx's Shell(), gated on
 * useIsDesktopNav()), replacing Sidebar's role on narrow viewports. Reads
 * TABS from lib/tabs.ts directly — never forks the tab list; the short
 * labels below are a local presentation override, not a new TabDef field.
 */
const PRIMARY_TABS: TabId[] = ['mission-control', 'live-signals', 'mission-center', 'research']

const NAV_LABEL_OVERRIDES: Partial<Record<TabId, string>> = {
  'mission-control': 'Home',
  'live-signals': 'Signals',
  'mission-center': 'Missions',
  research: 'Research',
}

const TAB_BY_ID = new Map(TABS.map((t) => [t.id as TabId, t]))

export function MobileBottomNav({
  tab,
  setTab,
  onMoreClick,
}: {
  tab: TabId
  setTab: (t: TabId) => void
  onMoreClick: () => void
}) {
  const onMoreTab = !PRIMARY_TABS.includes(tab)

  return (
    <nav
      aria-label="Primary"
      className="fixed inset-x-0 bottom-0 z-40 h-[var(--bottom-nav-h)] bg-panel border-t border-border flex items-stretch pb-[env(safe-area-inset-bottom)]"
    >
      {PRIMARY_TABS.map((id) => {
        const t = TAB_BY_ID.get(id)
        if (!t) return null
        const active = tab === id
        return (
          <button
            key={id}
            onClick={() => setTab(id)}
            aria-current={active ? 'page' : undefined}
            className={`flex-1 min-h-11 min-w-11 flex flex-col items-center justify-center gap-0.5 text-[0.62em] transition-colors ${
              active ? 'bg-accent/10 text-accent' : 'text-muted hover:text-text'
            }`}
          >
            <span className="text-[1.3em] leading-none">{t.glyph}</span>
            <span className="truncate max-w-full">{NAV_LABEL_OVERRIDES[id] ?? t.label}</span>
          </button>
        )
      })}
      <button
        onClick={onMoreClick}
        className={`flex-1 min-h-11 min-w-11 flex flex-col items-center justify-center gap-0.5 text-[0.62em] transition-colors ${
          onMoreTab ? 'bg-accent/10 text-accent' : 'text-muted hover:text-text'
        }`}
      >
        <span className="text-[1.3em] leading-none">⋯</span>
        <span>More</span>
      </button>
    </nav>
  )
}
