import { apiGet } from '../../lib/api'

// cTrader Connection (OAuth 2.0 web flow) — thin client over
// execution/routes/ctrader_auth.py's GET /ctrader/status. Never returns
// or accepts the raw access_token/refresh_token/client_secret.

export interface CtraderStatus {
  configured: boolean
  has_refresh_token: boolean
  account_id: string | null
  environment: string
  expires_at: number | null
  expires_in_seconds: number | null
  needs_reauthorization: boolean
}

export const getCtraderStatus = () => apiGet<CtraderStatus>('/ctrader/status')

// Full top-level navigation, NOT fetch — the cookie-based session must
// survive the external cTrader consent-page redirect round trip
// (execution/routes/auth.py's samesite="lax" comment is the existing
// precedent this relies on). A fetch() here would not carry the browser
// through cTrader's own consent page.
export function connectCtrader(): void {
  window.location.href = '/ctrader/authorize'
}
