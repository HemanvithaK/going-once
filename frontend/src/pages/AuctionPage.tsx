import { Link, useParams } from 'react-router'

/** Placeholder: the live auction page (snapshot, live price, bidding) is the next step. */
export function AuctionPage() {
  const { listingId } = useParams() // the :listingId part of the address
  return (
    <div>
      <Link to="/" className="text-sm text-indigo-700 hover:underline">
        ← All auctions
      </Link>
      <p className="mt-4 text-slate-600">
        The live auction page for <code className="text-xs">{listingId}</code> arrives in the
        next step.
      </p>
    </div>
  )
}
