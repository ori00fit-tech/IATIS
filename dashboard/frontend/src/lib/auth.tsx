import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from 'react'
import { apiGet, apiPost, ApiError } from './api'

type AuthStatus = 'checking' | 'authenticated' | 'unauthenticated'

interface AuthContextValue {
  status: AuthStatus
  login: (key: string) => Promise<void>
  logout: () => Promise<void>
  markUnauthenticated: () => void
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthStatus>('checking')

  useEffect(() => {
    // /budget is a lightweight authenticated endpoint — used purely as a session probe.
    apiGet('/budget')
      .then(() => setStatus('authenticated'))
      .catch((err) => {
        setStatus(err instanceof ApiError && err.status === 401 ? 'unauthenticated' : 'unauthenticated')
      })
  }, [])

  const login = useCallback(async (key: string) => {
    await apiPost('/login', { key })
    setStatus('authenticated')
  }, [])

  const logout = useCallback(async () => {
    try {
      await fetch('/logout', { credentials: 'include' })
    } finally {
      setStatus('unauthenticated')
    }
  }, [])

  const markUnauthenticated = useCallback(() => setStatus('unauthenticated'), [])

  return (
    <AuthContext.Provider value={{ status, login, logout, markUnauthenticated }}>{children}</AuthContext.Provider>
  )
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within AuthProvider')
  return ctx
}
