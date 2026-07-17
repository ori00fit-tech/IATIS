import type { ReactNode } from 'react'
import { Empty } from './Panel'
import { DiagnosticError } from './DiagnosticError'

/**
 * Shared loading/disabled/error shell for every AI-backed panel
 * (ai/ai_analyzer.py always returns status: 'ok' | 'disabled' | 'error').
 * Keeps the three call sites (trade explanation, macro, news, daily
 * report) from re-implementing the same four branches.
 *
 * Both failure paths (a fetch-level error and a provider-reported error) are
 * run through the v0.6 diagnostic taxonomy, so an operator sees a typed code
 * + a "where to look" hint instead of a bare string — `aiLayer` marks these
 * as AI-side so an otherwise-unclassified failure reads AI_PROVIDER_ERROR.
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
  if (fetchError) return <DiagnosticError message={fetchError} aiLayer />
  if (status === 'disabled') {
    return <Empty>{disabledHint ?? 'AI is disabled — set ai.enabled: true and an API key in config.yaml to turn this on.'}</Empty>
  }
  if (status === 'error') return <DiagnosticError message={providerError ?? 'unknown'} aiLayer />
  return <>{children}</>
}
