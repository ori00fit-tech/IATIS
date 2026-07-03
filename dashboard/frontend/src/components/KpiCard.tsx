type KpiColor = 'green' | 'red' | 'blue' | 'purple' | 'amber' | 'default'

const colorClass: Record<KpiColor, string> = {
  green: 'text-green',
  red: 'text-red',
  blue: 'text-accent',
  purple: 'text-accent2',
  amber: 'text-amber',
  default: 'text-text',
}

export function KpiCard({ value, label, color = 'default' }: { value: string | number; label: string; color?: KpiColor }) {
  return (
    <div className="relative overflow-hidden bg-card border border-border rounded-[10px] px-4 py-3.5">
      <div className="absolute top-0 left-0 right-0 h-0.5 bg-gradient-to-r from-accent to-accent2" />
      <div className={`text-[1.8em] font-extrabold leading-none mb-1 ${colorClass[color]}`}>{value}</div>
      <div className="text-[0.7em] text-muted uppercase tracking-[1px]">{label}</div>
    </div>
  )
}
