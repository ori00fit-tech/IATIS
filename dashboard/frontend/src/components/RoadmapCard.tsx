export function RoadmapCard({ title, note }: { title: string; note?: string }) {
  return (
    <div className="bg-card border border-border border-dashed rounded-[10px] p-4 opacity-60">
      <div className="text-[0.85em] font-bold text-muted mb-1">{title}</div>
      <div className="text-[0.72em] text-muted">{note ?? 'Not yet available — planned for v2'}</div>
    </div>
  )
}
