import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { services } from '../config'
import { authApi, type RegisterInput, type User } from '../api/auth'
import type { Session } from '../api/session'
import { AuthContext, type Auth, type AuthState } from './authContext'

interface Props {
  session: Session
  children: ReactNode
}

/**
 * Owns "who is signed in" for the whole app. Any component below it can call useAuth().
 *
 * React components re-draw when their state changes. useState holds a value that
 * survives re-draws; calling its setter tells React to draw again with the new value.
 */
export function AuthProvider({ session, children }: Props) {
  const [state, setState] = useState<AuthState>({ status: 'loading' })

  // useEffect runs after the first draw. Here: if a refresh token is stored, use it to
  // find out who we are. In development React runs this twice on purpose; the session's
  // single shared refresh makes that harmless.
  useEffect(() => {
    let cancelled = false
    async function restore() {
      if (!session.hasRefreshToken) {
        setState({ status: 'signedOut' })
        return
      }
      try {
        const user = await session.authorized<User>(`${services.api}/auth/me`)
        if (!cancelled) setState({ status: 'signedIn', user })
      } catch {
        if (!cancelled) setState({ status: 'signedOut' })
      }
    }
    void restore()
    return () => {
      cancelled = true // the component went away; do not touch its state any more
    }
  }, [session])

  const login = useCallback(
    async (email: string, password: string) => {
      const tokens = await authApi.login(email, password)
      session.save(tokens)
      const user = await authApi.me(tokens.access_token)
      setState({ status: 'signedIn', user })
    },
    [session],
  )

  const register = useCallback(
    async (input: RegisterInput) => {
      await authApi.register(input) // creates the account but returns no tokens
      await login(input.email, input.password)
    },
    [login],
  )

  const logout = useCallback(async () => {
    const token = session.refreshToken
    session.clear()
    setState({ status: 'signedOut' })
    if (token !== null) {
      // Tell the server to revoke it. If that fails, the token is already gone from here.
      await authApi.logout(token).catch(() => undefined)
    }
  }, [session])

  const value = useMemo<Auth>(
    () => ({ state, login, register, logout }),
    [state, login, register, logout],
  )
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}
