import { useMemo, useState } from 'react'
import { Command } from 'cmdk'
import { TABS, SECTION_ORDER, type TabId } from '../lib/tabs'

/**
 * ⌘K / Ctrl-K jump-to-module palette (institutional-redesign Phase 1 —
 * rewritten on cmdk, same external contract and jump-to-tab-only behavior
 * as the hand-rolled version it replaces). shouldFilter is disabled and the
 * original substring match kept verbatim, rather than switching to cmdk's
 * default fuzzy scorer, so this isn't a silent behavior change alongside
 * the visual one. Command *actions* beyond navigation are a later phase.
 *
 * Command.Dialog composition (verified against cmdk's source, not assumed):
 * `overlayClassName` styles the Radix Dialog Overlay (the dark backdrop),
 * `contentClassName` styles the Radix Dialog Content (the positioning
 * wrapper — Radix doesn't center content by default), and the top-level
 * `className` lands on the inner cmdk root div, which doubles here as the
 * actual palette "box" styling. There is no single `className` that reaches
 * the overlay/content pair.
 */
export function CommandPalette({
  open,
  activeTab,
  onSelect,
  onClose,
}: {
  open: boolean
  activeTab: TabId
  onSelect: (tab: TabId) => void
  onClose: () => void
}) {
  const [query, setQuery] = useState('')

  const bySection = useMemo(() => {
    const q = query.trim().toLowerCase()
    const matches = !q
      ? TABS
      : TABS.filter((t) => t.label.toLowerCase().includes(q) || t.id.includes(q) || t.hint.toLowerCase().includes(q))
    return SECTION_ORDER.map((section) => ({ section, tabs: matches.filter((t) => t.section === section) })).filter(
      (g) => g.tabs.length > 0
    )
  }, [query])

  const commit = (tab: TabId) => {
    onSelect(tab)
    onClose()
  }

  return (
    <Command.Dialog
      open={open}
      onOpenChange={(v) => !v && onClose()}
      shouldFilter={false}
      label="Jump to module"
      overlayClassName="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm"
      contentClassName="fixed inset-0 z-50 flex items-start justify-center pt-[12vh] px-4"
      className="w-full max-w-[560px] bg-card border border-border rounded-xl overflow-hidden shadow-2xl"
    >
      <div className="flex items-center gap-2 px-4 py-3 border-b border-border">
        <span className="text-muted text-[0.9em]">⌕</span>
        <Command.Input
          value={query}
          onValueChange={setQuery}
          placeholder="Jump to module…"
          className="flex-1 bg-transparent border-none outline-none text-text text-[0.95em] placeholder:text-muted"
        />
        <kbd className="text-[0.62em] text-muted border border-border rounded px-1.5 py-0.5">ESC</kbd>
      </div>
      <Command.List className="max-h-[52vh] overflow-y-auto py-1">
        <Command.Empty className="px-4 py-6 text-center text-muted text-[0.82em]">No module matches “{query}”</Command.Empty>
        {bySection.map(({ section, tabs }) => (
          <Command.Group
            key={section}
            heading={section}
            className="[&_[cmdk-group-heading]]:px-4 [&_[cmdk-group-heading]]:py-1.5 [&_[cmdk-group-heading]]:text-[0.68em] [&_[cmdk-group-heading]]:font-bold [&_[cmdk-group-heading]]:uppercase [&_[cmdk-group-heading]]:tracking-[1px] [&_[cmdk-group-heading]]:text-muted"
          >
            {tabs.map((t) => (
              <Command.Item
                key={t.id}
                value={t.id}
                onSelect={() => commit(t.id as TabId)}
                className="flex items-center gap-3 px-4 py-2.5 cursor-pointer data-[selected=true]:bg-accent/10"
              >
                <span className="text-[1em] w-5 text-center text-muted [[data-selected=true]_&]:text-accent">{t.glyph}</span>
                <span className="flex flex-col min-w-0">
                  <span className="text-[0.86em] font-semibold truncate text-text [[data-selected=true]_&]:text-accent">
                    {t.label}
                    {t.id === activeTab && <span className="ml-2 text-[0.72em] text-muted font-normal">current</span>}
                  </span>
                  <span className="text-[0.72em] text-muted truncate">{t.hint}</span>
                </span>
              </Command.Item>
            ))}
          </Command.Group>
        ))}
      </Command.List>
    </Command.Dialog>
  )
}
