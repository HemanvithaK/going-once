import { useState, type FormEvent } from 'react'
import { Link, Navigate } from 'react-router'
import { ApiError } from '../api/client'
import { useAuth } from '../auth/authContext'
import { FormError } from '../components/FormError'
import { TextField } from '../components/TextField'

// The same limits the API enforces (services/api/app/schemas/auth.py). Checking here
// gives instant feedback; the API still checks, because anyone can skip this page.
const MIN_PASSWORD = 12
const MIN_NAME = 2

export function RegisterPage() {
  const { state, register } = useAuth()
  const [displayName, setDisplayName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  if (state.status === 'signedIn') {
    return <Navigate to="/" replace />
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (displayName.trim().length < MIN_NAME) {
      setError(`display name must be at least ${MIN_NAME} characters`)
      return
    }
    if (password.length < MIN_PASSWORD) {
      setError(`password must be at least ${MIN_PASSWORD} characters`)
      return
    }
    setError(null)
    setBusy(true)
    try {
      await register({ email, password, display_name: displayName.trim() }) // then redirected
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'something went wrong')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="mx-auto max-w-sm">
      <h1 className="text-2xl font-bold">Create an account</h1>
      <form onSubmit={handleSubmit} className="mt-6 space-y-4" noValidate>
        <TextField
          label="Display name"
          autoComplete="nickname"
          value={displayName}
          onChange={(e) => setDisplayName(e.target.value)}
        />
        <TextField
          label="Email"
          type="email"
          autoComplete="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
        />
        <TextField
          label="Password"
          type="password"
          autoComplete="new-password"
          hint={`At least ${MIN_PASSWORD} characters.`}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
        <FormError message={error} />
        <button
          type="submit"
          disabled={busy}
          className="w-full rounded-md bg-indigo-600 px-4 py-2 font-medium text-white hover:bg-indigo-700 disabled:opacity-60"
        >
          {busy ? 'Creating account…' : 'Create account'}
        </button>
      </form>
      <p className="mt-4 text-sm text-slate-600">
        Already have an account?{' '}
        <Link to="/login" className="text-indigo-700 hover:underline">
          Log in
        </Link>
      </p>
    </div>
  )
}
