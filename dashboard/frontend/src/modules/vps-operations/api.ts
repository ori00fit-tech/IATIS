import { apiPost } from '../../lib/api'

export interface ReloadConfigResponse {
  success: boolean
  message: string
}

export const reloadConfig = () => apiPost<ReloadConfigResponse>('/ops/reload-config')
