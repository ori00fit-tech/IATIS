import { motion } from 'framer-motion'
import { TABS, SECTION_ORDER, type TabId } from '../lib/tabs'
import { useUiStore } from '../lib/uiStore'
import { Tooltip, TooltipProvider } from './ui/Tooltip'
import { Collapsible, CollapsibleTrigger, CollapsibleContent } from './ui/Collapsible'
import { ScrollArea } from './ui/ScrollArea'

/**
 * Grouped left-nav shell (institutional-redesign Phase 1) replacing the
 * flat horizontal tab strip. Reads TABS/SECTION_ORDER from lib/tabs.ts
 * (the single source of truth CommandPalette also uses) and drives
 * navigation through the exact same useHashTab tab/setTab pair the old
 * <nav> used — no new routing logic, every existing hash URL keeps
 * resolving identically.
 */
export function Sidebar({ tab, setTab }: { tab: TabId; setTab: (t: TabId) => void }) {
  const collapsed = useUiStore((s) => s.sidebarCollapsed)
  const toggleCollapsed = useUiStore((s) => s.toggleSidebarCollapsed)
  const expandedSections = useUiStore((s) => s.expandedSections)
  const toggleSection = useUiStore((s) => s.toggleSection)

  const bySection = SECTION_ORDER.map((section) => ({
    section,
    tabs: TABS.filter((t) => t.section === section),
  })).filter((g) => g.tabs.length > 0)

  return (
    <TooltipProvider>
      <motion.aside
        animate={{ width: collapsed ? 56 : 240 }}
        transition={{ duration: 0.15, ease: 'easeInOut' }}
        className="shrink-0 border-r border-border bg-panel flex flex-col overflow-hidden"
        aria-label="Modules"
      >
        <ScrollArea className="flex-1">
          <nav className="flex flex-col gap-1 p-2">
            {bySection.map(({ section, tabs }) => {
              const isExpanded = collapsed ? true : (expandedSections[section] ?? true)
              return (
                <Collapsible key={section} open={isExpanded} onOpenChange={() => !collapsed && toggleSection(section)}>
                  {!collapsed && (
                    <CollapsibleTrigger className="w-full flex items-center justify-between px-2 py-1.5 text-[0.68em] font-bold uppercase tracking-[1px] text-muted hover:text-text">
                      <span>{section}</span>
                      <motion.span animate={{ rotate: isExpanded ? 90 : 0 }} transition={{ duration: 0.15 }}>
                        ›
                      </motion.span>
                    </CollapsibleTrigger>
                  )}
                  <CollapsibleContent forceMount asChild>
                    <motion.div
                      initial={false}
                      animate={isExpanded ? { height: 'auto', opacity: 1 } : { height: 0, opacity: 0 }}
                      transition={{ duration: 0.15, ease: 'easeInOut' }}
                      className="overflow-hidden flex flex-col gap-0.5"
                    >
                      {tabs.map((t) => {
                        const active = tab === t.id
                        const item = (
                          <button
                            key={t.id}
                            onClick={() => setTab(t.id as TabId)}
                            aria-current={active ? 'page' : undefined}
                            title={collapsed ? undefined : t.hint}
                            className={`flex items-center gap-2.5 rounded px-2 py-1.5 text-[0.8em] text-left transition-colors w-full ${
                              active ? 'bg-accent/10 text-accent' : 'text-muted hover:text-text hover:bg-surface/60'
                            } ${collapsed ? 'justify-center' : ''}`}
                          >
                            <span className="text-[1.05em] leading-none shrink-0">{t.glyph}</span>
                            {!collapsed && <span className="truncate">{t.label}</span>}
                          </button>
                        )
                        return collapsed ? (
                          <Tooltip
                            key={t.id}
                            content={
                              <>
                                <div className="font-bold">{t.label}</div>
                                <div className="text-muted">{t.hint}</div>
                              </>
                            }
                          >
                            {item}
                          </Tooltip>
                        ) : (
                          item
                        )
                      })}
                    </motion.div>
                  </CollapsibleContent>
                </Collapsible>
              )
            })}
          </nav>
        </ScrollArea>

        <button
          onClick={toggleCollapsed}
          className="flex items-center justify-center gap-2 px-2 py-2.5 text-[0.72em] text-muted border-t border-border hover:text-accent shrink-0"
          title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
        >
          <motion.span animate={{ rotate: collapsed ? 180 : 0 }} transition={{ duration: 0.15 }}>
            ‹
          </motion.span>
          {!collapsed && <span>Collapse</span>}
        </button>
      </motion.aside>
    </TooltipProvider>
  )
}
