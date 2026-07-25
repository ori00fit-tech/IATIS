import { useState } from 'react'
import { queryClient } from '../../lib/queryClient'
import { ApiError } from '../../lib/api'
import { uploadDataset } from './api'

const TIMEFRAMES = ['M1', 'M5', 'M15', 'M30', 'H1', 'H4', 'D1', 'W1'] as const

/**
 * CSV/Parquet dataset upload (Phase 4a, 2026-07-25) — lets an operator add a
 * local historical OHLCV dataset without shelling into the VPS to run
 * scripts/download_all_symbols.py. One-shot mutation, not a poll, so this
 * keeps its own local state rather than useApiQuery. Refreshes the Dataset
 * Explorer table below it (['research-datasets'], the same key DatasetStep
 * already polls under) via queryClient.invalidateQueries on success.
 */
export function DatasetUpload() {
  const [symbol, setSymbol] = useState('')
  const [timeframe, setTimeframe] = useState<(typeof TIMEFRAMES)[number]>('H1')
  const [file, setFile] = useState<File | null>(null)
  const [overwrite, setOverwrite] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<string | null>(null)

  const submit = async () => {
    if (!file) {
      setError('Choose a .csv or .parquet file first')
      return
    }
    setSubmitting(true)
    setError(null)
    setResult(null)
    try {
      const form = new FormData()
      form.set('symbol', symbol)
      form.set('timeframe', timeframe)
      form.set('overwrite', overwrite ? 'true' : 'false')
      form.set('file', file)
      const res = await uploadDataset(form)
      setResult(`${res.file}: ${res.rows} rows (${res.start?.slice(0, 10) ?? '—'} → ${res.end?.slice(0, 10) ?? '—'})`)
      setFile(null)
      void queryClient.invalidateQueries({ queryKey: ['research-datasets'] })
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="p-4 border-b border-border flex flex-col gap-2">
      <div className="text-[0.78em] text-muted uppercase tracking-[1.5px] font-bold">Upload dataset</div>
      <div className="flex items-center gap-2 flex-wrap">
        <input
          value={symbol}
          onChange={(e) => setSymbol(e.target.value.toUpperCase())}
          placeholder="SYMBOL"
          className="w-32 px-2 py-1.5 text-[0.82em] bg-bg border border-border rounded text-text placeholder:text-muted/60"
        />
        <select
          value={timeframe}
          onChange={(e) => setTimeframe(e.target.value as (typeof TIMEFRAMES)[number])}
          className="px-2 py-1.5 text-[0.82em] bg-bg border border-border rounded text-text"
        >
          {TIMEFRAMES.map((tf) => (
            <option key={tf} value={tf}>
              {tf}
            </option>
          ))}
        </select>
        <input
          type="file"
          accept=".csv,.parquet"
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          className="text-[0.78em] text-muted"
        />
        <label className="flex items-center gap-1.5 text-[0.78em] text-muted">
          <input type="checkbox" checked={overwrite} onChange={(e) => setOverwrite(e.target.checked)} />
          Overwrite existing
        </label>
        <button
          onClick={() => void submit()}
          disabled={submitting || !symbol || !file}
          className="px-3 py-1.5 text-[0.82em] font-bold rounded border border-accent text-accent hover:bg-accent/10 disabled:opacity-40 disabled:cursor-not-allowed"
        >
          {submitting ? 'Uploading…' : 'Upload'}
        </button>
      </div>
      {error && <div className="text-[0.78em] text-red">{error}</div>}
      {result && <div className="text-[0.78em] text-green">Uploaded {result}</div>}
    </div>
  )
}
