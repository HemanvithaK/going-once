import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { Session } from '../api/session'
import App from '../App'
import { AuthProvider } from '../auth/AuthProvider'

/** Draw the whole app at an address, with a fresh session and cache, the way main.tsx does. */
export function renderApp(path = '/') {
  const session = new Session(window.localStorage)
  // retry: false, so a test that expects an error sees it at once instead of after retries.
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[path]}>
        <AuthProvider session={session}>
          <App />
        </AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return session
}
