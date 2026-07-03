type DotState = 'ok' | 'warn' | 'err' | 'loading'

const stateClass: Record<DotState, string> = {
  ok: 'bg-green shadow-[0_0_6px_var(--green)]',
  warn: 'bg-amber shadow-[0_0_6px_var(--amber)]',
  err: 'bg-red shadow-[0_0_6px_var(--red)]',
  loading: 'bg-amber shadow-[0_0_6px_var(--amber)]',
}

export function StatusDot({ state }: { state: DotState }) {
  return <span className={`inline-block w-2 h-2 rounded-full animate-[pulse-dot_2s_infinite] ${stateClass[state]}`} />
}

export function StatusRow({ label, state, detail }: { label: string; state: DotState; detail?: string }) {
  return (
    <div className="flex items-center justify-between px-3 py-2 text-[0.82em] border-b border-border last:border-b-0">
      <div className="flex items-center gap-2">
        <StatusDot state={state} />
        <span>{label}</span>
      </div>
      {detail && <span className="text-muted">{detail}</span>}
    </div>
  )
}
