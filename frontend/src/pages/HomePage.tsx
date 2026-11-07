import { Link } from 'react-router'
import { useAuth } from '../auth/authContext'

export function HomePage() {
  const { state } = useAuth()

  if (state.status === 'loading') {
    return <p className="text-slate-500">Loading…</p>
  }
  if (state.status === 'signedOut') {
    return (
      <div>
        <h1 className="text-2xl font-bold">Live auctions</h1>
        <p className="mt-2 text-slate-600">
          <Link to="/login" className="text-indigo-700 hover:underline">
            Log in
          </Link>{' '}
          to bid. Auction listings arrive in the next step.
        </p>
      </div>
    )
  }
  return (
    <div>
      <h1 className="text-2xl font-bold">Welcome, {state.user.display_name}</h1>
      <p className="mt-2 text-slate-600">Auction listings arrive in the next step.</p>
    </div>
  )
}
