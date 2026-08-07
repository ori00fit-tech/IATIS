import type { ReactNode } from 'react'

export function Panel({
  title,
  right,
  children,
  clipContent = true,
}: {
  title: string
  right?: ReactNode
  children: ReactNode
  /**
   * Mobile-First Restructuring Phase 2 follow-up (2026-08-06) — default
   * true, byte-identical to every existing consumer. This wrapper's own
   * overflow-hidden exists so a child's edge-to-edge background (a table
   * row, a hover state) gets clipped to the panel's rounded corners — but
   * it also makes this div the nearest "scrolling ancestor" per the CSS
   * spec, which silently breaks position:sticky for anything nested
   * inside (it never actually reaches the real scroll container further
   * up the tree). Pass false only when a panel needs a working sticky
   * child and has no edge-to-edge child backgrounds to clip — e.g.
   * MissionCenter.tsx's "New Mission" panel's sticky Launch bar.
   */
  clipContent?: boolean
}) {
  return (
    <div className={`bg-card border border-border rounded-[10px] ${clipContent ? 'overflow-hidden' : ''}`}>
      <div className="flex items-center justify-between px-4 py-3 border-b border-border">
        <span className="text-[0.8em] font-bold text-accent uppercase tracking-[1.5px]">{title}</span>
        {right && <span className="text-[0.7em] text-muted">{right}</span>}
      </div>
      <div>{children}</div>
    </div>
  )
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="p-8 text-center text-muted text-[0.85em]">{children}</div>
}
