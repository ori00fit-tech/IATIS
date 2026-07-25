import { useEffect, useState } from 'react'
import { usePolling } from './usePolling'
import { useAuth } from './auth'
import { getLogs, getLogSources, type LogSource } from '../modules/live-logs/api'

const POLL_MS = 10_000
const DEFAULT_SOURCE = 'api'

export function levelClass(line: string) {
  const upper = line.toUpperCase()
  if (upper.includes('ERROR') || upper.includes('CRITICAL')) return 'text-red'
  if (upper.includes('WARN')) return 'text-amber'
  return 'text-text'
}

/**
 * Shared log-fetching logic behind both the full Live Logs module tab and
 * the compact bottom-panel Logs view (Phase 1 institutional-redesign shell)
 * — one data source, two renderings. Factored out of LiveLogs.tsx rather
 * than duplicated so the bottom panel never becomes a second, drifting
 * copy of the polling/search-state logic.
 */
export function useLiveLogs(defaultLines = 200) {
  const { markUnauthenticated } = useAuth()
  const [sources, setSources] = useState<LogSource[]>([])
  const [source, setSource] = useState(DEFAULT_SOURCE)
  const [lines, setLines] = useState(defaultLines)
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

  return { logs, sources: sourceOptions, source, setSource, lines, setLines, search, setSearch, appliedSearch, setAppliedSearch }
}
