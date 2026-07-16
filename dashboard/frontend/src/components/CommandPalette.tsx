import { useEffect, useMemo, useRef, useState } from 'react'
import { TABS, type TabId } from '../lib/tabs'

/**
 * ⌘K / Ctrl-K jump-to-module palette. Fifteen tabs is past the point where a
 * horizontal scroll strip is a good primary navigation — this gives keyboard
 * operators a fuzzy, arrow-driven switch that never leaves the home row.
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
  const [cursor, setCursor] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)

  const results = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return TABS
    return TABS.filter(
      (t) => t.label.toLowerCase().includes(q) || t.id.includes(q) || t.hint.toLowerCase().includes(q),
    )
  }, [query])

  // Reset transient state each time the palette opens.
  useEffect(() => {
    if (open) {
      setQuery('')
      setCursor(0)
      // Focus after the element is actually in the DOM.
      requestAnimationFrame(() => inputRef.current?.focus())
    }
  }, [open])

  // Keep the highlighted row in range as the result set shrinks.
  useEffect(() => {
    setCursor((c) => Math.min(c, Math.max(0, results.length - 1)))
  }, [results.length])

  if (!open) return null

  const commit = (tab: TabId) => {
    onSelect(tab)
    onClose()
  }

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setCursor((c) => Math.min(c + 1, results.length - 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setCursor((c) => Math.max(c - 1, 0))
    } else if (e.key === 'Enter') {
      e.preventDefault()
      const pick = results[cursor]
      if (pick) commit(pick.id as TabId)
    } else if (e.key === 'Escape') {
      e.preventDefault()
      onClose()
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center bg-black/60 backdrop-blur-sm pt-[12vh] px-4"
      onMouseDown={onClose}
      role="dialog"
      aria-modal="true"
      aria-label="Jump to module"
    >
      <div
        className="w-full max-w-[560px] bg-card border border-border rounded-xl overflow-hidden shadow-2xl"
        onMouseDown={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-2 px-4 py-3 border-b border-border">
          <span className="text-muted text-[0.9em]">⌕</span>
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder="Jump to module…"
            className="flex-1 bg-transparent border-none outline-none text-text text-[0.95em] placeholder:text-muted"
          />
          <kbd className="text-[0.62em] text-muted border border-border rounded px-1.5 py-0.5">ESC</kbd>
        </div>
        <ul className="max-h-[52vh] overflow-y-auto py-1">
          {results.length === 0 && (
            <li className="px-4 py-6 text-center text-muted text-[0.82em]">No module matches “{query}”</li>
          )}
          {results.map((t, i) => (
            <li key={t.id}>
              <button
                onMouseEnter={() => setCursor(i)}
                onClick={() => commit(t.id as TabId)}
                className={`w-full flex items-center gap-3 px-4 py-2.5 text-left transition-colors ${
                  i === cursor ? 'bg-accent/10' : ''
                }`}
              >
                <span className={`text-[1em] w-5 text-center ${i === cursor ? 'text-accent' : 'text-muted'}`}>{t.glyph}</span>
                <span className="flex flex-col min-w-0">
                  <span className={`text-[0.86em] font-semibold truncate ${i === cursor ? 'text-accent' : 'text-text'}`}>
                    {t.label}
                    {t.id === activeTab && <span className="ml-2 text-[0.72em] text-muted font-normal">current</span>}
                  </span>
                  <span className="text-[0.72em] text-muted truncate">{t.hint}</span>
                </span>
              </button>
            </li>
          ))}
        </ul>
      </div>
    </div>
  )
}
