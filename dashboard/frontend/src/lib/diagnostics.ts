// Diagnostic error taxonomy (Command Center v0.6, cross-cutting §). Replaces
// generic "error" strings with a typed reason so an operator knows *where* to
// look. Classification is pure and runs client-side: the frontend already
// knows which panel raised the error (AI vs data) and ApiError.message carries
// the HTTP status ("429 /ai/news-analysis"), so no backend change is needed —
// though an explicit `error_code` from the server, if ever added, wins.

export type DiagCode =
  | 'PROVIDER_UNAVAILABLE'
  | 'AUTH_FAILED'
  | 'RATE_LIMITED'
  | 'TIMEOUT'
  | 'BAD_FORMAT'
  | 'AI_PROVIDER_ERROR'
  | 'UNKNOWN'

export interface Diagnostic {
  code: DiagCode
  /** Short operator-facing label. */
  label: string
  /** Where to look / what to do. */
  hint: string
}

const CATALOG: Record<DiagCode, { label: string; hint: string }> = {
  PROVIDER_UNAVAILABLE: {
    label: 'Provider unavailable',
    hint: 'Upstream host unreachable or returned 5xx. Check provider status and network connectivity.',
  },
  AUTH_FAILED: {
    label: 'Auth failed',
    hint: 'Rejected with 401/403 — the API key is missing, wrong, or lacks permission. Check .env / config.',
  },
  RATE_LIMITED: {
    label: 'Rate limited',
    hint: '429 / quota exhausted. Wait and retry, or check the provider budget on Mission Control.',
  },
  TIMEOUT: {
    label: 'Timeout',
    hint: 'The request exceeded its time budget. Retry; if it persists the upstream is slow or stalled.',
  },
  BAD_FORMAT: {
    label: 'Bad response',
    hint: 'The response was unexpected or unparseable — usually a provider-side change. Check server logs.',
  },
  AI_PROVIDER_ERROR: {
    label: 'AI layer error',
    hint: 'The AI layer failed (distinct from the data providers). Check ai.enabled and the AI API key.',
  },
  UNKNOWN: {
    label: 'Unclassified error',
    hint: 'Could not classify the failure. See Live Logs / server logs for the raw error.',
  },
}

const make = (code: DiagCode): Diagnostic => ({ code, ...CATALOG[code] })

/** Map an HTTP status to a taxonomy code (null when it isn't error-shaped). */
function fromHttpStatus(status: number): DiagCode | null {
  if (status === 401 || status === 403) return 'AUTH_FAILED'
  if (status === 429) return 'RATE_LIMITED'
  if (status === 408 || status === 504) return 'TIMEOUT'
  if (status === 400 || status === 422) return 'BAD_FORMAT'
  if (status >= 500) return 'PROVIDER_UNAVAILABLE'
  return null
}

/** Keyword-match a provider error string — mirrors ai_analyzer._user_safe_error. */
function fromMessage(text: string): DiagCode | null {
  const t = text.toLowerCase()
  if (t.includes('timed out') || t.includes('timeout')) return 'TIMEOUT'
  if (t.includes('429') || t.includes('rate limit') || t.includes('quota')) return 'RATE_LIMITED'
  if (t.includes('401') || t.includes('403') || t.includes('unauthorized') || t.includes('permission') || t.includes('api key'))
    return 'AUTH_FAILED'
  if (
    t.includes('name or service not known') ||
    t.includes('failed to resolve') ||
    t.includes('connection') ||
    t.includes('unreachable') ||
    t.includes('could not reach') ||
    /\b5\d\d\b/.test(t)
  )
    return 'PROVIDER_UNAVAILABLE'
  if (t.includes('response shape') || t.includes('no text content') || t.includes('non-json') || t.includes('unexpected') || t.includes('unparse'))
    return 'BAD_FORMAT'
  return null
}

/**
 * Classify an error surface into the taxonomy.
 * - `httpStatus`: the numeric status if known (from ApiError).
 * - `message`: any error/provider text (ApiError.message begins with the
 *   status, so it's parsed too).
 * - `aiLayer`: true when the surface is an AI panel — an otherwise-unclassified
 *   failure there is an AI_PROVIDER_ERROR rather than a generic UNKNOWN.
 */
export function classifyError(input: { httpStatus?: number; message?: string | null; aiLayer?: boolean }): Diagnostic {
  const { httpStatus, message, aiLayer } = input

  if (typeof httpStatus === 'number') {
    const code = fromHttpStatus(httpStatus)
    if (code) return make(code)
  }
  if (message) {
    // ApiError.message is `${status} ${path}` — pull a leading status first.
    const lead = message.match(/^\s*(\d{3})\b/)
    if (lead) {
      const code = fromHttpStatus(Number(lead[1]))
      if (code) return make(code)
    }
    const code = fromMessage(message)
    if (code) return make(code)
  }
  return make(aiLayer ? 'AI_PROVIDER_ERROR' : 'UNKNOWN')
}
