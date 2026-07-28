import { apiGet, apiPost } from '../../lib/api'

// AI Settings (2026-07-28) — thin client over execution/routes/ai.py's
// GET/POST /ai/settings. Only controls the explanation/reporting layer's
// config/ai.yaml (never the decision path — see ai/ai_analyzer.py's own
// docstring).

export const KNOWN_AI_PROVIDERS = ['gemini', 'openai', 'anthropic'] as const
export type AiProvider = (typeof KNOWN_AI_PROVIDERS)[number]

export interface AiSettingsResponse {
  enabled: boolean
  providers: Record<string, { enabled: boolean }>
  fallback_order: string[]
  model: string
  temperature: number
  max_tokens: number
  timeout: number
  default_models: Record<string, string>
  has_api_key: Record<string, boolean>
  active_provider: string
}

export interface AiSettingsSaveRequest {
  enabled: boolean
  providers: Record<string, { enabled: boolean }>
  fallback_order: string[]
  model: string
  temperature: number
  max_tokens: number
  timeout: number
}

export const getAiSettings = () => apiGet<AiSettingsResponse>('/ai/settings')

export const saveAiSettings = (body: AiSettingsSaveRequest) =>
  apiPost<{ status: string; active_provider: string }>('/ai/settings', body)
