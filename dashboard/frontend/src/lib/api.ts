export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  // FormData bodies (Phase 4a dataset upload) must never get an explicit
  // Content-Type — the browser sets its own multipart boundary, which we
  // can't reproduce by hand.
  const isFormData = init?.body instanceof FormData
  const res = await fetch(path, {
    credentials: 'include',
    ...init,
    headers: {
      ...(isFormData ? {} : { 'Content-Type': 'application/json' }),
      ...init?.headers,
    },
  })
  if (!res.ok) {
    // FastAPI puts the human-readable reason in {"detail": ...} — surface it
    // instead of the bare "400 /path" that operators can't act on.
    let detail = ''
    try {
      const body = (await res.json()) as { detail?: unknown }
      if (typeof body.detail === 'string') detail = body.detail
    } catch {
      /* non-JSON error body — keep the bare status */
    }
    throw new ApiError(res.status, `${res.status} ${path}${detail ? ` — ${detail}` : ''}`)
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

/** POST a multipart/form-data body (Phase 4a dataset upload). */
export function apiPostFormData<T>(path: string, form: FormData): Promise<T> {
  return request<T>(path, { method: 'POST', body: form })
}

/** GET a text/plain endpoint (the Prometheus /metrics exposition). */
export async function apiGetText(path: string): Promise<string> {
  const res = await fetch(path, { credentials: 'include' })
  if (!res.ok) {
    throw new ApiError(res.status, `${res.status} ${path}`)
  }
  return res.text()
}
