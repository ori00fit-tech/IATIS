import * as Dialog from '@radix-ui/react-dialog'
import { motion, AnimatePresence } from 'framer-motion'
import { TABS, SECTION_ORDER, type TabId } from '../../lib/tabs'

/**
 * Mobile-First Restructuring Phase 1 (2026-08-06) — bottom-sheet listing
 * all tabs grouped by SECTION_ORDER, for tabs not pinned in
 * MobileBottomNav. Section grouping mirrors the same 3-line expression
 * Sidebar.tsx/CommandPalette.tsx already compute inline (duplicated here
 * rather than extracted to a shared helper — not worth touching
 * Sidebar.tsx in a minimal-diff phase). Uses @radix-ui/react-dialog
 * directly (already a direct dependency, previously only reached
 * transitively via cmdk's Command.Dialog) + framer-motion for the
 * slide-up, matching Sidebar's existing motion timing — no new
 * dependency.
 */
const bySection = SECTION_ORDER.map((section) => ({
  section,
  tabs: TABS.filter((t) => t.section === section),
})).filter((g) => g.tabs.length > 0)

export function MoreNavDrawer({
  open,
  onOpenChange,
  tab,
  setTab,
}: {
  open: boolean
  onOpenChange: (v: boolean) => void
  tab: TabId
  setTab: (t: TabId) => void
}) {
  const commit = (id: TabId) => {
    setTab(id)
    onOpenChange(false)
  }

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <AnimatePresence>
        {open && (
          <Dialog.Portal forceMount>
            <Dialog.Overlay asChild forceMount>
              <motion.div
                className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                transition={{ duration: 0.15, ease: 'easeInOut' }}
              />
            </Dialog.Overlay>
            <Dialog.Content asChild forceMount aria-describedby={undefined}>
              <motion.div
                className="fixed inset-x-0 bottom-0 z-50 max-h-[80vh] overflow-y-auto rounded-t-2xl bg-panel border-t border-border pb-[env(safe-area-inset-bottom)]"
                initial={{ y: '100%' }}
                animate={{ y: 0 }}
                exit={{ y: '100%' }}
                transition={{ duration: 0.2, ease: 'easeInOut' }}
              >
                <div className="flex items-center justify-between px-4 py-3 border-b border-border sticky top-0 bg-panel">
                  <Dialog.Title className="text-[0.85em] font-bold text-text">All Modules</Dialog.Title>
                  <Dialog.Close className="text-muted text-[0.85em] px-2 py-1">✕</Dialog.Close>
                </div>
                <nav className="flex flex-col gap-1 p-2">
                  {bySection.map(({ section, tabs }) => (
                    <div key={section} className="flex flex-col gap-0.5">
                      <div className="px-2 py-1.5 text-[0.68em] font-bold uppercase tracking-[1px] text-muted">
                        {section}
                      </div>
                      {tabs.map((t) => {
                        const active = tab === t.id
                        return (
                          <button
                            key={t.id}
                            onClick={() => commit(t.id as TabId)}
                            aria-current={active ? 'page' : undefined}
                            className={`flex items-center gap-2.5 rounded px-2 py-2.5 min-h-11 text-[0.8em] text-left transition-colors w-full ${
                              active ? 'bg-accent/10 text-accent' : 'text-muted hover:text-text hover:bg-surface/60'
                            }`}
                          >
                            <span className="text-[1.05em] leading-none shrink-0">{t.glyph}</span>
                            <span className="truncate">{t.label}</span>
                          </button>
                        )
                      })}
                    </div>
                  ))}
                </nav>
              </motion.div>
            </Dialog.Content>
          </Dialog.Portal>
        )}
      </AnimatePresence>
    </Dialog.Root>
  )
}
