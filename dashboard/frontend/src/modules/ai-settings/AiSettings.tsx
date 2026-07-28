import { useEffect, useState } from 'react'
import { Panel, Empty } from '../../components/Panel'
import { Badge } from '../../components/Badge'
import { useApiQuery } from '../../lib/useApiQuery'
import { useAuth } from '../../lib/auth'
import { KNOWN_AI_PROVIDERS, getAiSettings, saveAiSettings } from './api'

const POLL_MS = 15000

export function AiSettings() {
  const { markUnauthenticated } = useAuth()
  const settingsQuery = useApiQuery(['ai-settings'], getAiSettings, POLL_MS, markUnauthenticated)

  const [enabled, setEnabled] = useState(false)
  const [providers, setProviders] = useState<Record<string, boolean>>({})
  const [fallbackOrder, setFallbackOrder] = useState<string[]>([...KNOWN_AI_PROVIDERS])
  const [model, setModel] = useState('')
  const [temperature, setTemperature] = useState(0.1)
  const [maxTokens, setMaxTokens] = useState(1200)
  const [timeout, setTimeoutSec] = useState(20)
  const [loaded, setLoaded] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    if (settingsQuery.data && !loaded) {
      const d = settingsQuery.data
      setEnabled(d.enabled)
      setProviders(Object.fromEntries(KNOWN_AI_PROVIDERS.map((p) => [p, d.providers[p]?.enabled ?? false])))
      setFallbackOrder(d.fallback_order.length > 0 ? d.fallback_order : [...KNOWN_AI_PROVIDERS])
      setModel(d.model)
      setTemperature(d.temperature)
      setMaxTokens(d.max_tokens)
      setTimeoutSec(d.timeout)
      setLoaded(true)
    }
  }, [settingsQuery.data, loaded])

  if (!settingsQuery.data) return <Panel title="AI Settings"><Empty>Loading…</Empty></Panel>
  const hasKey = settingsQuery.data.has_api_key
  const defaultModels = settingsQuery.data.default_models

  const moveInFallback = (provider: string, dir: -1 | 1) => {
    setFallbackOrder((prev) => {
      const idx = prev.indexOf(provider)
      const swapWith = idx + dir
      if (swapWith < 0 || swapWith >= prev.length) return prev
      const next = [...prev]
      ;[next[idx], next[swapWith]] = [next[swapWith], next[idx]]
      return next
    })
  }

  const save = async () => {
    setSaving(true)
    setError(null)
    setSaved(false)
    try {
      const result = await saveAiSettings({
        enabled,
        providers: Object.fromEntries(KNOWN_AI_PROVIDERS.map((p) => [p, { enabled: providers[p] ?? false }])),
        fallback_order: fallbackOrder,
        model,
        temperature,
        max_tokens: maxTokens,
        timeout,
      })
      setSaved(true)
      void result
      settingsQuery.refetch()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="flex flex-col gap-4 p-4">
      <Panel title="AI Settings" right="explanation/reporting layer only — never the trading decision path">
        <div className="p-4 flex flex-col gap-4">
          <div className="text-[0.78em] text-muted">
            Active provider right now: <Badge tone={hasKey[settingsQuery.data.active_provider] ? 'good' : 'poor'}>
              {settingsQuery.data.active_provider}
            </Badge>
          </div>

          <label className="flex items-center gap-2 text-[0.85em]">
            <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
            AI explanations enabled
          </label>

          <div className="flex flex-col gap-2">
            <span className="text-[0.7em] text-muted uppercase">Providers (fallback order, top = tried first)</span>
            {fallbackOrder.map((p, i) => (
              <div key={p} className="flex items-center gap-3 text-[0.82em]">
                <span className="w-6 text-muted">{i + 1}.</span>
                <label className="flex items-center gap-2 w-28">
                  <input type="checkbox" checked={providers[p] ?? false}
                    onChange={(e) => setProviders((prev) => ({ ...prev, [p]: e.target.checked }))} />
                  {p}
                </label>
                <Badge tone={hasKey[p] ? 'good' : 'poor'}>{hasKey[p] ? 'API key set' : 'no API key'}</Badge>
                <button onClick={() => moveInFallback(p, -1)} disabled={i === 0}
                  className="text-muted hover:text-accent disabled:opacity-30 text-[0.8em]">↑</button>
                <button onClick={() => moveInFallback(p, 1)} disabled={i === fallbackOrder.length - 1}
                  className="text-muted hover:text-accent disabled:opacity-30 text-[0.8em]">↓</button>
              </div>
            ))}
          </div>

          <div className="flex flex-wrap gap-3 items-end">
            <div className="flex flex-col gap-1">
              <span className="text-[0.7em] text-muted uppercase">Model</span>
              <input value={model} onChange={(e) => setModel(e.target.value)}
                placeholder={defaultModels[settingsQuery.data!.active_provider] ?? ''}
                className="bg-surface border border-border rounded px-2 py-1.5 text-[0.78em] text-text w-56" />
            </div>
            <div className="flex flex-col gap-1">
              <span className="text-[0.7em] text-muted uppercase">Temperature</span>
              <input type="number" step={0.05} min={0} max={2} value={temperature}
                onChange={(e) => setTemperature(Number(e.target.value))}
                className="bg-surface border border-border rounded px-2 py-1.5 text-[0.78em] text-text w-24" />
            </div>
            <div className="flex flex-col gap-1">
              <span className="text-[0.7em] text-muted uppercase">Max tokens</span>
              <input type="number" min={1} max={8000} value={maxTokens}
                onChange={(e) => setMaxTokens(Number(e.target.value))}
                className="bg-surface border border-border rounded px-2 py-1.5 text-[0.78em] text-text w-28" />
            </div>
            <div className="flex flex-col gap-1">
              <span className="text-[0.7em] text-muted uppercase">Timeout (s)</span>
              <input type="number" min={1} max={120} value={timeout}
                onChange={(e) => setTimeoutSec(Number(e.target.value))}
                className="bg-surface border border-border rounded px-2 py-1.5 text-[0.78em] text-text w-24" />
            </div>
          </div>

          {error && <div className="text-red text-[0.8em]">{error}</div>}
          {saved && !error && <div className="text-green text-[0.8em]">Saved.</div>}
          <div>
            <button onClick={save} disabled={saving}
              className="px-4 py-2 rounded bg-accent text-bg font-bold text-[0.82em] disabled:opacity-50">
              {saving ? 'Saving…' : 'Save Settings'}
            </button>
          </div>
        </div>
      </Panel>
    </div>
  )
}
