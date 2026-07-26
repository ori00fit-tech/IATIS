import { useRef, useState } from 'react'
import { usePresetsStore, type WizardPreset } from './presetsStore'
import type { WizardState } from './BacktestingLab'

/**
 * Presets ("FX H4"/"Crypto H1" quick-load) + shareable Profile export/
 * import (Phase 4b, 2026-07-26) — mounted once above the wizard's step
 * content so a preset can restore multiple steps' state at once. See
 * presetsStore.ts for why this is frontend-only (localStorage), not a
 * new backend endpoint.
 */
export function PresetsBar({ state, setState }: { state: WizardState; setState: (s: WizardState) => void }) {
  const { presets, savePreset, deletePreset } = usePresetsStore()
  const presetNames = Object.keys(presets).sort()

  const [nameInput, setNameInput] = useState('')
  const [selected, setSelected] = useState('')
  const [importError, setImportError] = useState<string | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const applyPreset = (p: WizardPreset) => {
    setState({
      selectedSymbols: p.selectedSymbols,
      jobId: p.jobId,
      sweepParams: p.sweepParams,
      sweepMultipliers: p.sweepMultipliers,
    })
  }

  const handleLoad = (name: string) => {
    setSelected(name)
    const p = presets[name]
    if (p) applyPreset(p)
  }

  const handleSave = () => {
    const name = nameInput.trim()
    if (!name) return
    savePreset({ name, ...state, savedAt: new Date().toISOString() })
    setSelected(name)
    setNameInput('')
  }

  const handleDelete = () => {
    if (!selected) return
    deletePreset(selected)
    setSelected('')
  }

  const handleExport = () => {
    const name = (selected || nameInput.trim() || 'untitled').trim()
    const preset: WizardPreset = { name, ...state, savedAt: new Date().toISOString() }
    const blob = new Blob([JSON.stringify(preset, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${name.replace(/[^a-z0-9_-]+/gi, '_')}.json`
    a.click()
    URL.revokeObjectURL(url)
  }

  const handleImportFile = (file: File) => {
    setImportError(null)
    const reader = new FileReader()
    reader.onload = () => {
      let parsed: unknown
      try {
        parsed = JSON.parse(String(reader.result))
      } catch {
        setImportError('Not valid JSON')
        return
      }
      const preset = validatePreset(parsed, file.name)
      if (!preset) {
        setImportError("File doesn't look like a wizard preset (needs selectedSymbols: string[] and jobId: string)")
        return
      }
      savePreset(preset)
      setSelected(preset.name)
      applyPreset(preset)
    }
    reader.onerror = () => setImportError('Could not read the file')
    reader.readAsText(file)
  }

  return (
    <div className="flex items-center gap-2 flex-wrap px-4 pb-3 text-[0.82em]">
      <span className="text-muted uppercase text-[0.78em] tracking-[1px]">Presets</span>
      <select
        value={selected}
        onChange={(e) => handleLoad(e.target.value)}
        className="px-2 py-1.5 bg-bg border border-border rounded text-text min-w-[140px]"
      >
        <option value="">— load a preset —</option>
        {presetNames.map((n) => (
          <option key={n} value={n}>
            {n}
          </option>
        ))}
      </select>
      <button
        onClick={handleDelete}
        disabled={!selected}
        className="px-2.5 py-1.5 rounded border border-red/40 text-red hover:bg-red/10 disabled:opacity-30 disabled:cursor-not-allowed"
      >
        Delete
      </button>
      <span className="w-px h-5 bg-border" />
      <input
        value={nameInput}
        onChange={(e) => setNameInput(e.target.value)}
        placeholder="preset name…"
        className="w-40 px-2 py-1.5 bg-bg border border-border rounded text-text placeholder:text-muted/60"
      />
      <button
        onClick={handleSave}
        disabled={!nameInput.trim()}
        className="px-2.5 py-1.5 rounded border border-accent text-accent hover:bg-accent/10 disabled:opacity-40 disabled:cursor-not-allowed"
      >
        Save current
      </button>
      <button
        onClick={handleExport}
        className="px-2.5 py-1.5 rounded border border-border text-muted hover:text-accent hover:border-accent/50"
      >
        Export ↓
      </button>
      <button
        onClick={() => fileInputRef.current?.click()}
        className="px-2.5 py-1.5 rounded border border-border text-muted hover:text-accent hover:border-accent/50"
      >
        Import ↑
      </button>
      <input
        ref={fileInputRef}
        type="file"
        accept=".json,application/json"
        className="hidden"
        onChange={(e) => {
          const file = e.target.files?.[0]
          if (file) handleImportFile(file)
          e.target.value = ''
        }}
      />
      {importError && <span className="text-red text-[0.85em]">{importError}</span>}
    </div>
  )
}

function validatePreset(value: unknown, fallbackName: string): WizardPreset | null {
  if (typeof value !== 'object' || value === null) return null
  const v = value as Record<string, unknown>
  if (!Array.isArray(v.selectedSymbols) || !v.selectedSymbols.every((s) => typeof s === 'string')) return null
  if (typeof v.jobId !== 'string' || v.jobId.length === 0) return null
  const sweepParams = Array.isArray(v.sweepParams) ? v.sweepParams.filter((s): s is string => typeof s === 'string') : []
  const sweepMultipliers = Array.isArray(v.sweepMultipliers)
    ? v.sweepMultipliers.filter((n): n is number => typeof n === 'number')
    : []
  const name = typeof v.name === 'string' && v.name.trim() ? v.name.trim() : fallbackName.replace(/\.json$/i, '')
  const savedAt = typeof v.savedAt === 'string' ? v.savedAt : new Date().toISOString()
  return { name, selectedSymbols: v.selectedSymbols, jobId: v.jobId, sweepParams, sweepMultipliers, savedAt }
}
