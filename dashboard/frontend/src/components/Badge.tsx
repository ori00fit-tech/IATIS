type BadgeTone = 'exec' | 'no-trade' | 'good' | 'marginal' | 'poor' | 'neutral'

const toneClass: Record<BadgeTone, string> = {
  exec: 'bg-green/15 text-green',
  'no-trade': 'bg-red/10 text-red',
  good: 'bg-accent/12 text-accent',
  marginal: 'bg-amber/12 text-amber',
  poor: 'bg-red/10 text-red',
  neutral: 'bg-border text-muted',
}

export function Badge({ tone, children }: { tone: BadgeTone; children: string }) {
  return <span className={`inline-block px-2 py-0.5 rounded text-[0.75em] font-bold ${toneClass[tone]}`}>{children}</span>
}
