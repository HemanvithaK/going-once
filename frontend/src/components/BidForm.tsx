import { useState, type FormEvent } from 'react'
import { placeBid, type BidResult } from '../api/bids'
import { ApiError } from '../api/client'
import { useAuth } from '../auth/authContext'
import { formatCents, parseCents } from '../lib/money'
import { FormError } from './FormError'

interface Props {
  listingId: string
  suggested: string // current price plus one increment
  onPlaced: (result: BidResult) => void
}

interface Attempt {
  amount: string
  key: string
}

/**
 * The bid form. Each attempt to bid an amount gets one Idempotency-Key. If the answer is
 * lost (status 0: we cannot tell whether the bid landed), trying again re-sends the same
 * key, so the bidding service returns the bid it already has instead of placing another.
 */
export function BidForm({ listingId, suggested, onPlaced }: Props) {
  const { session } = useAuth()
  // null means "follow the suggestion": the box shows the live price plus one increment
  // until the user types their own amount, and goes back to following it after a bid lands.
  const [typed, setTyped] = useState<string | null>(null)
  const amount = typed ?? suggested
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [unanswered, setUnanswered] = useState<Attempt | null>(null)

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const cents = parseCents(amount)
    if (cents === null) {
      setError('enter an amount like 150 or 150.50')
      return
    }
    const normalized = formatCents(cents)
    // Same amount as an attempt that got no answer: same key. Anything else: a new attempt.
    const key =
      unanswered !== null && unanswered.amount === normalized ? unanswered.key : crypto.randomUUID()

    setError(null)
    setBusy(true)
    try {
      const result = await placeBid(session, listingId, normalized, key)
      setUnanswered(null)
      setTyped(null)
      onPlaced(result)
    } catch (err) {
      if (err instanceof ApiError && err.status === 0) {
        setUnanswered({ amount: normalized, key }) // outcome unknown: keep the key for a retry
        setError('No answer from the server. Try again: the same bid cannot be placed twice.')
      } else {
        setUnanswered(null) // the server answered, so this attempt is finished
        setError(err instanceof ApiError ? err.message : 'something went wrong')
      }
    } finally {
      setBusy(false)
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-3" noValidate>
      <label htmlFor="bid-amount" className="block text-sm font-medium text-slate-700">
        Your maximum bid
      </label>
      <div className="flex gap-2">
        <div className="relative flex-1">
          <span className="pointer-events-none absolute inset-y-0 left-3 flex items-center text-slate-500">
            $
          </span>
          <input
            id="bid-amount"
            inputMode="decimal"
            autoComplete="off"
            value={amount}
            onChange={(e) => setTyped(e.target.value)}
            className="block w-full rounded-md border border-slate-300 py-2 pr-3 pl-7 tabular-nums shadow-sm focus:border-indigo-500 focus:ring-2 focus:ring-indigo-200 focus:outline-none"
          />
        </div>
        <button
          type="submit"
          disabled={busy}
          className="rounded-md bg-indigo-600 px-4 py-2 font-medium text-white hover:bg-indigo-700 disabled:opacity-60"
        >
          {busy ? 'Bidding…' : 'Place bid'}
        </button>
      </div>
      <p className="text-xs text-slate-500">
        You only pay enough to beat the next highest bid, never more than your maximum.
      </p>
      <FormError message={error} />
    </form>
  )
}
