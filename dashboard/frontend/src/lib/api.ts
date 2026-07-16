export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    credentials: 'include',
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...init?.headers,
    },
  })
  if (!res.ok) {
    throw new ApiError(res.status, `${res.status} ${path}`)
  }
  return (await res.json()) as T
}

export function apiGet<T>(path: string, params?: Record<string, string | number | boolean | undefined>): Promise<T> {
  const query = params
    ? '?' +
      Object.entries(params)
        .filter(([, v]) => v !== undefined)
        .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`)
        .join('&')
    : ''
  return request<T>(`${path}${query && query !== '?' ? query : ''}`)
}

export function apiPost<T>(path: string, body?: unknown): Promise<T> {
  return request<T>(path, { method: 'POST', body: body ? JSON.stringify(body) : undefined })
}

/** GET a text/plain endpoint (the Prometheus /metrics exposition). */
export async function apiGetText(path: string): Promise<string> {
  const res = await fetch(path, { credentials: 'include' })
  if (!res.ok) {
    throw new ApiError(res.status, `${res.status} ${path}`)
  }
  return res.text()
}
