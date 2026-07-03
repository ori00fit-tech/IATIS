import type { ReactNode } from 'react'

export function Panel({
  title,
  right,
  children,
}: {
  title: string
  right?: ReactNode
  children: ReactNode
}) {
  return (
    <div className="bg-card border border-border rounded-[10px] overflow-hidden">
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
