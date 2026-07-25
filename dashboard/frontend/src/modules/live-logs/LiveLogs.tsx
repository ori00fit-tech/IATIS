import { Panel, Empty } from '../../components/Panel'
import { useLiveLogs, levelClass } from '../../lib/useLiveLogs'

const input = 'bg-surface border border-border rounded px-2 py-1.5 text-[0.82em] text-text placeholder:text-muted'

export function LiveLogs() {
  const { logs, sources: sourceOptions, source, setSource, lines, setLines, search, setSearch, setAppliedSearch } = useLiveLogs(200)

  return (
    <div className="flex flex-col gap-4">
      <Panel title="Live Logs" right={logs.data ? `${logs.data.lines_returned} lines` : undefined}>
        <div className="flex flex-wrap items-end gap-2 px-4 py-3 border-b border-border bg-surface/40">
          <label className="flex flex-col gap-1">
            <span className="text-[0.68em] text-muted uppercase tracking-[0.8px]">Source</span>
            <select className={input} value={source} onChange={(e) => setSource(e.target.value)}>
              {sourceOptions.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.label}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-[0.68em] text-muted uppercase tracking-[0.8px]">Lines</span>
            <input
              className={`${input} w-20`}
              type="number"
              min={1}
              max={1000}
              value={lines}
              onChange={(e) => setLines(Number(e.target.value) || 200)}
            />
          </label>
          <label className="flex flex-col gap-1 min-w-[220px]">
            <span className="text-[0.68em] text-muted uppercase tracking-[0.8px]">Search</span>
            <input
              className={input}
              placeholder="filter lines..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && setAppliedSearch(search)}
            />
          </label>
          <div className="flex gap-2 pb-0.5">
            <button onClick={() => setAppliedSearch(search)} className="px-3 py-1.5 text-[0.78em] rounded bg-accent/15 text-accent hover:bg-accent/25">
              Apply
            </button>
            <button
              onClick={() => {
                setSearch('')
                setAppliedSearch('')
              }}
              className="px-3 py-1.5 text-[0.78em] rounded text-muted hover:text-text"
            >
              Clear
            </button>
            <button onClick={() => logs.refetch()} className="px-3 py-1.5 text-[0.78em] rounded text-muted hover:text-text">
              Refresh
            </button>
          </div>
        </div>

        {logs.data?.error && <div className="px-4 py-2 text-[0.8em] text-amber border-b border-border">{logs.data.error}</div>}

        {logs.data && logs.data.entries.length > 0 ? (
          <pre className="p-4 text-[0.78em] leading-relaxed overflow-auto max-h-[600px] whitespace-pre-wrap break-words font-mono">
            {logs.data.entries.map((line, i) => (
              <div key={i} className={levelClass(line)}>
                {line}
              </div>
            ))}
          </pre>
        ) : (
          <Empty>{logs.loading ? 'Loading...' : 'No log lines'}</Empty>
        )}
      </Panel>
    </div>
  )
}
