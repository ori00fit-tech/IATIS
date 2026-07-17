import { classifyError, type DiagCode, type Diagnostic } from '../lib/diagnostics'

// Colour by how the operator should react: auth/format are config-side (amber),
// rate/timeout are transient (accent), unavailable/AI-layer are upstream (red).
const toneByCode: Record<DiagCode, string> = {
  AUTH_FAILED: 'border-amber/40 text-amber',
  BAD_FORMAT: 'border-amber/40 text-amber',
  RATE_LIMITED: 'border-accent/40 text-accent',
  TIMEOUT: 'border-accent/40 text-accent',
  PROVIDER_UNAVAILABLE: 'border-red/40 text-red',
  AI_PROVIDER_ERROR: 'border-red/40 text-red',
  UNKNOWN: 'border-border text-muted',
}

/**
 * Renders a classified failure: a typed code chip + operator hint + the raw
 * detail. Accepts either an already-classified Diagnostic or the raw inputs
 * (httpStatus/message/aiLayer) and classifies them.
 */
export function DiagnosticError({
  diagnostic,
  httpStatus,
  message,
  aiLayer,
}: {
  diagnostic?: Diagnostic
  httpStatus?: number
  message?: string | null
  aiLayer?: boolean
}) {
  const diag = diagnostic ?? classifyError({ httpStatus, message, aiLayer })
  return (
    <div className="p-6 flex flex-col items-center gap-2 text-center">
      <span className={`inline-flex items-center gap-1.5 text-[0.68em] font-bold uppercase tracking-[1px] border rounded px-2 py-0.5 ${toneByCode[diag.code]}`}>
        <span className="font-mono">{diag.code}</span>
      </span>
      <div className="text-[0.85em] text-text">{diag.label}</div>
      <div className="text-[0.78em] text-muted max-w-[440px]">{diag.hint}</div>
      {message && <div className="text-[0.7em] text-muted/70 max-w-[440px] font-mono break-words">{message}</div>}
    </div>
  )
}
