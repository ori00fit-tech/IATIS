import { create } from 'zustand'
import { persist } from 'zustand/middleware'

interface NotesState {
  text: string
  setText: (text: string) => void
}

// Persisted (2026-07-26) — the mirror case of consoleCapture.ts: a
// scratchpad an operator is actively using across a session should
// survive a reload, unlike ephemeral per-page-load diagnostics.
// Single shared scratchpad, no per-note identity — this app has zero
// per-user identity anywhere in the backend (see presetsStore.ts),
// and a scratchpad has no multi-note need stated to justify one.
export const useNotesStore = create<NotesState>()(
  persist(
    (set) => ({
      text: '',
      setText: (text) => set({ text }),
    }),
    { name: 'iatis.research-notes' },
  ),
)
