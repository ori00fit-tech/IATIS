import { useState, type FormEvent } from 'react'
import { useAuth } from '../lib/auth'
import { ApiError } from '../lib/api'

export function Login() {
  const { login } = useAuth()
  const [key, setKey] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    if (!key.trim()) return
    setSubmitting(true)
    setError(null)
    try {
      await login(key.trim())
    } catch (err) {
      setError(err instanceof ApiError && err.status === 401 ? 'Invalid key — try again' : 'Connection error')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center">
      <form onSubmit={onSubmit} className="bg-card border border-border rounded-xl p-10 w-80 text-center">
        <h1 className="text-accent m-0 mb-2">⚡ IATIS</h1>
        <p className="text-muted text-[0.85em] m-0 mb-6">Enter your API key to access the Command Center</p>
        <input
          type="password"
          autoFocus
          value={key}
          onChange={(e) => setKey(e.target.value)}
          placeholder="API Server Key"
          className="w-full box-border px-2.5 py-2.5 bg-bg border border-border rounded-md text-text text-base mb-3 outline-none focus:border-accent"
        />
        <button
          type="submit"
          disabled={submitting}
          className="w-full px-2.5 py-2.5 bg-green border-none rounded-md text-white text-base cursor-pointer disabled:opacity-60"
        >
          {submitting ? 'Connecting...' : 'Login'}
        </button>
        {error && <div className="text-red text-[0.85em] mt-2">{error}</div>}
      </form>
    </div>
  )
}
