import { useEffect, useState } from 'react'
import { usePolling } from '../../lib/usePolling'
import { useAuth } from '../../lib/auth'
import { Panel, Empty } from '../../components/Panel'
import { getLogs, getLogSources, type LogSource } from './api'

const POLL_MS = 10_000
const DEFAULT_SOURCE = 'api'
const input = 'bg-surface border border-border rounded px-2 py-1.5 text-[0.82em] text-text placeholder:text-muted'

function levelClass(line: string) {
  const upper = line.toUpperCase()
  if (upper.includes('ERROR') || upper.includes('CRITICAL')) return 'text-red'
  if (upper.includes('WARN')) return 'text-amber'
  return 'text-text'
}

export function LiveLogs() {
  const { markUnauthenticated } = useAuth()
  const [sources, setSources] = useState<LogSource[]>([])
  const [source, setSource] = useState(DEFAULT_SOURCE)
  const [lines, setLines] = useState(200)
  const [search, setSearch] = useState('')
  const [appliedSearch, setAppliedSearch] = useState('')

  useEffect(() => {
    getLogSources()
      .then((r) => setSources(r.sources))
      .catch(() => {})
  }, [])

  const logs = usePolling(() => getLogs(source, lines, appliedSearch), POLL_MS, markUnauthenticated)

  useEffect(() => {
    logs.refetch()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [source, lines, appliedSearch])

  const sourceOptions = sources.length ? sources : [{ id: DEFAULT_SOURCE, label: 'api', kind: 'journal' as const }]

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
