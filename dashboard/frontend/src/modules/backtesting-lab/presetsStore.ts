import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import type { DateRangeState, RiskOverridesState } from './BacktestingLab'

/**
 * Named, reusable Backtesting Lab wizard configurations (Phase 4b,
 * 2026-07-26) — "presets" (quick-load, e.g. "FX H4") and "shareable
 * Profile files" from the original spec are the same underlying
 * mechanism, covered together here.
 *
 * Module-scoped (not global like src/lib/uiStore.ts) since this is
 * specific to the Backtesting Lab wizard's own state shape. Own
 * localStorage key, same reasoning as uiStore.ts: never collide with
 * another store's persisted data.
 *
 * Frontend-only by design — this app has a single shared API-key/session
 * credential with no per-user identity anywhere in the backend, so a
 * backend-persisted "presets" table would just be one global list every
 * operator shares, with new attack surface for no real benefit over
 * localStorage + the export/import file mechanism in PresetsBar.tsx.
 */
export interface WizardPreset {
  name: string
  selectedSymbols: string[]
  jobId: string
  sweepParams: string[]
  sweepMultipliers: number[]
  // Backtesting Lab Pro Phase A (2026-07-27) — optional so a preset saved
  // before this phase still loads (PresetsBar.tsx's applyPreset/
  // validatePreset fall back to defaults when absent).
  dateRange?: DateRangeState
  riskOverrides?: RiskOverridesState
  savedAt: string
}

interface PresetsState {
  presets: Record<string, WizardPreset>
  savePreset: (preset: WizardPreset) => void
  deletePreset: (name: string) => void
}

export const usePresetsStore = create<PresetsState>()(
  persist(
    (set) => ({
      presets: {},
      savePreset: (preset) => set((s) => ({ presets: { ...s.presets, [preset.name]: preset } })),
      deletePreset: (name) =>
        set((s) => {
          const next = { ...s.presets }
          delete next[name]
          return { presets: next }
        }),
    }),
    { name: 'iatis.backtesting-lab.presets' },
  ),
)
