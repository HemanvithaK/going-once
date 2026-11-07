import { Link } from 'react-router'
import type { Listing } from '../api/listings'
import { formatMoney, timeLeft } from '../lib/format'

interface Props {
  listing: Listing
  now: number // passed in, so one clock drives every card on the page
}

export function ListingCard({ listing, now }: Props) {
  const left = timeLeft(listing.ends_at, now)
  const closingSoon = Date.parse(listing.ends_at) - now < 5 * 60 * 1000

  return (
    <Link
      to={`/auctions/${listing.id}`}
      className="block rounded-lg border border-slate-200 bg-white p-4 shadow-sm transition hover:border-indigo-300 hover:shadow"
    >
      <h2 className="truncate font-semibold">{listing.title}</h2>
      <p className="mt-3 text-2xl font-bold tabular-nums">{formatMoney(listing.current_price)}</p>
      <p className="text-xs text-slate-500">current price</p>
      <div className="mt-4 flex items-center justify-between text-sm">
        <span className={closingSoon ? 'font-medium text-red-600' : 'text-slate-600'}>
          {left === 'ending' ? 'Ending now' : `Ends in ${left}`}
        </span>
        <span className="rounded bg-slate-100 px-2 py-0.5 text-xs text-slate-600">
          {listing.has_reserve ? 'Reserve' : 'No reserve'}
        </span>
      </div>
    </Link>
  )
}
