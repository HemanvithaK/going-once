import { Link, Outlet } from 'react-router'
import { useAuth } from '../auth/authContext'

/** The frame around every page. <Outlet /> is where the current page is drawn. */
export function Layout() {
  const { state, logout } = useAuth()

  return (
    <div className="min-h-screen bg-slate-50 text-slate-900">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-5xl items-center justify-between px-4 py-4">
          <Link to="/" className="text-xl font-bold tracking-tight">
            going-once
          </Link>
          <nav className="flex items-center gap-4 text-sm">
            {state.status === 'signedIn' && (
              <>
                <span className="text-slate-600">{state.user.display_name}</span>
                <button
                  type="button"
                  onClick={() => void logout()}
                  className="rounded-md border border-slate-300 px-3 py-1.5 hover:bg-slate-100"
                >
                  Log out
                </button>
              </>
            )}
            {state.status === 'signedOut' && (
              <>
                <Link to="/login" className="hover:underline">
                  Log in
                </Link>
                <Link
                  to="/register"
                  className="rounded-md bg-indigo-600 px-3 py-1.5 font-medium text-white hover:bg-indigo-700"
                >
                  Sign up
                </Link>
              </>
            )}
          </nav>
        </div>
      </header>
      <main className="mx-auto max-w-5xl px-4 py-10">
        <Outlet />
      </main>
    </div>
  )
}
