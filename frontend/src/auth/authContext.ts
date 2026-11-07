import { createContext, useContext } from 'react'
import type { RegisterInput, User } from '../api/auth'
import type { Session } from '../api/session'

// "loading" lasts only while the app checks for a stored login at startup.
export type AuthState =
  | { status: 'loading' }
  | { status: 'signedOut' }
  | { status: 'signedIn'; user: User }

export interface Auth {
  state: AuthState
  login: (email: string, password: string) => Promise<void>
  register: (input: RegisterInput) => Promise<void>
  logout: () => Promise<void>
  session: Session // for calls that need a login, such as placing a bid
}

export const AuthContext = createContext<Auth | null>(null)

/** Read the auth state from any component inside <AuthProvider>. */
export function useAuth(): Auth {
  const auth = useContext(AuthContext)
  if (auth === null) {
    throw new Error('useAuth must be used inside <AuthProvider>')
  }
  return auth
}
