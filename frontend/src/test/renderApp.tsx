import { render } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { Session } from '../api/session'
import App from '../App'
import { AuthProvider } from '../auth/AuthProvider'

/** Draw the whole app at an address, with a fresh session, the way main.tsx does. */
export function renderApp(path = '/') {
  const session = new Session(window.localStorage)
  render(
    <MemoryRouter initialEntries={[path]}>
      <AuthProvider session={session}>
        <App />
      </AuthProvider>
    </MemoryRouter>,
  )
  return session
}
