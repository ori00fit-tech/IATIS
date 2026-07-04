import type { ReactNode } from 'react'
import { Empty } from './Panel'

/**
 * Shared loading/disabled/error shell for every AI-backed panel
 * (ai/ai_analyzer.py always returns status: 'ok' | 'disabled' | 'error').
 * Keeps the three call sites (trade explanation, macro, news, daily
 * report) from re-implementing the same four branches.
 */
export function AiStatusFrame({
  loading,
  fetchError,
  status,
  providerError,
  disabledHint,
  children,
}: {
  loading: boolean
  fetchError: string | null
  status?: 'ok' | 'disabled' | 'error'
  providerError?: string
  disabledHint?: string
  children: ReactNode
}) {
  if (loading) return <Empty>Asking the AI provider…</Empty>
  if (fetchError) return <Empty>Request failed: {fetchError}</Empty>
  if (status === 'disabled') {
    return <Empty>{disabledHint ?? 'AI is disabled — set ai.enabled: true and an API key in config.yaml to turn this on.'}</Empty>
  }
  if (status === 'error') return <Empty>Provider error: {providerError ?? 'unknown'}</Empty>
  return <>{children}</>
}
