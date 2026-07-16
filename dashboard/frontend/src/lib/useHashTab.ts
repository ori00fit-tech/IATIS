import { useCallback, useEffect, useState } from 'react'
import { TABS, isTabId, type TabId } from './tabs'

const STORAGE_KEY = 'iatis.activeTab'
const DEFAULT_TAB: TabId = TABS[0].id as TabId

function readInitial(): TabId {
  const fromHash = window.location.hash.replace(/^#\/?/, '')
  if (fromHash && isTabId(fromHash)) return fromHash
  try {
    const stored = localStorage.getItem(STORAGE_KEY)
    if (stored && isTabId(stored)) return stored
  } catch {
    // localStorage can throw in private mode — fall through to default.
  }
  return DEFAULT_TAB
}

/**
 * Single source of truth for the active module: URL hash (deep-linkable,
 * back/forward-aware) mirrored to localStorage (survives reloads). Before
 * this the console always reopened on Mission Control and no tab could be
 * shared as a link — awkward for a 15-module operations surface.
 */
export function useHashTab(): [TabId, (tab: TabId) => void] {
  const [tab, setTabState] = useState<TabId>(readInitial)

  // Keep hash + storage in sync whenever the tab changes.
  useEffect(() => {
    if (window.location.hash !== `#/${tab}`) {
      window.history.replaceState(null, '', `#/${tab}`)
    }
    try {
      localStorage.setItem(STORAGE_KEY, tab)
    } catch {
      /* ignore */
    }
  }, [tab])

  // React to browser back/forward and manual hash edits.
  useEffect(() => {
    const onHashChange = () => {
      const next = window.location.hash.replace(/^#\/?/, '')
      if (isTabId(next)) setTabState(next)
    }
    window.addEventListener('hashchange', onHashChange)
    return () => window.removeEventListener('hashchange', onHashChange)
  }, [])

  const setTab = useCallback((next: TabId) => {
    window.history.pushState(null, '', `#/${next}`)
    setTabState(next)
  }, [])

  return [tab, setTab]
}
