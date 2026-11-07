import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useLocation, useParams } from 'react-router'
import type { BidResult } from '../api/bids'
import { ApiError } from '../api/client'
import { listingsApi } from '../api/listings'
import { useAuth } from '../auth/authContext'
import { BidForm } from '../components/BidForm'
import { formatMoney, timeLeft } from '../lib/format'
import { addMoney, parseCents } from '../lib/money'
import { useNow } from '../lib/useNow'
import type { Connection } from '../realtime/auctionFeed'
import { useAuctionFeed } from '../realtime/useAuctionFeed'

/** The route's page. Keyed by id, so moving to another auction starts from a clean slate. */
export function AuctionPage() {
  const { listingId = '' } = useParams()
  return <AuctionView key={listingId} listingId={listingId} />
}

function AuctionView({ listingId }: { listingId: string }) {
  const { state: auth } = useAuth()
  const location = useLocation()
  const now = useNow()
  const listing = useQuery({
    queryKey: ['listing', listingId],
    queryFn: () => listingsApi.get(listingId),
  })
  const { state: live, connection, history } = useAuctionFeed(listingId)
  const [myBid, setMyBid] = useState<BidResult | null>(null)

  if (connection === 'not_found' || (listing.error instanceof ApiError && listing.error.status === 404)) {
    return (
      <div>
        <BackLink />
        <p className="mt-4 text-slate-600">This auction does not exist, or is not public yet.</p>
      </div>
    )
  }
  if (listing.isPending) {
    return <p className="text-slate-500">Loading auction…</p>
  }
  if (listing.isError) {
    return (
      <div role="alert" className="rounded-md bg-red-50 p-4 text-red-700">
        Could not load this auction: {listing.error instanceof ApiError ? listing.error.message : 'unknown error'}
      </div>
    )
  }

  const info = listing.data
  // The live feed is the truth for price, deadline and status. The API's copy can lag.
  const price = live?.current_price ?? info.current_price
  const endsAt = live?.ends_at ?? info.ends_at
  const status = live?.status ?? info.status
  const isSeller = auth.status === 'signedIn' && auth.user.id === info.seller_id
  const outbid =
    myBid !== null && (parseCents(price) ?? 0) > (parseCents(myBid.max_amount) ?? 0)
  const leading = myBid !== null && myBid.is_leading && !outbid
  // Bidding's rule: the leader may only raise their own maximum, by at least one increment.
  // Everyone else must beat the current price by at least one increment.
  const suggested = addMoney(leading ? myBid.max_amount : price, info.bid_increment)

  return (
    <div>
      <BackLink />
      <div className="mt-4 grid gap-8 lg:grid-cols-[1fr_380px]">
        <section>
          <h1 className="text-3xl font-bold tracking-tight">{info.title}</h1>
          {info.description && (
            <p className="mt-4 whitespace-pre-line text-slate-700">{info.description}</p>
          )}
          <dl className="mt-6 grid grid-cols-3 gap-4 text-sm">
            <Fact label="Starting price" value={formatMoney(info.start_price)} />
            <Fact label="Bid increment" value={formatMoney(info.bid_increment)} />
            <Fact label="Reserve" value={info.has_reserve ? 'Yes' : 'None'} />
          </dl>
        </section>

        <aside className="rounded-lg border border-slate-200 bg-white p-6 shadow-sm">
          <div className="flex items-center justify-between">
            <span className="text-sm text-slate-500">
              {status === 'live' ? 'Current price' : 'Final price'}
            </span>
            <ConnectionBadge connection={connection} />
          </div>
          {/* key={version}: React re-creates the element on each update, replaying the flash. */}
          <p
            key={live?.version ?? 'initial'}
            className="mt-1 animate-price-flash rounded text-4xl font-bold tabular-nums"
          >
            {formatMoney(price)}
          </p>

          {status === 'live' ? (
            <p className="mt-2 text-sm text-slate-600">
              {timeLeft(endsAt, now) === 'ending' ? 'Ending now' : `Ends in ${timeLeft(endsAt, now)}`}
            </p>
          ) : (
            <p className="mt-3 rounded-md bg-slate-100 px-3 py-2 text-sm font-medium">
              {status === 'sold' ? `Sold for ${formatMoney(price)}` : 'Ended without a sale'}
            </p>
          )}

          {myBid !== null && status === 'live' && (
            <p
              className={`mt-4 rounded-md px-3 py-2 text-sm ${
                outbid || !myBid.is_leading ? 'bg-amber-50 text-amber-800' : 'bg-green-50 text-green-800'
              }`}
            >
              {outbid || !myBid.is_leading
                ? `You've been outbid. Your maximum was ${formatMoney(myBid.max_amount)}.`
                : `You're the highest bidder, up to your maximum of ${formatMoney(myBid.max_amount)}.`}
            </p>
          )}

          <div className="mt-6">
            {status !== 'live' ? null : auth.status === 'signedOut' ? (
              <Link
                to="/login"
                state={{ from: location.pathname }}
                className="block rounded-md bg-indigo-600 px-4 py-2 text-center font-medium text-white hover:bg-indigo-700"
              >
                Log in to bid
              </Link>
            ) : isSeller ? (
              <p className="text-sm text-slate-600">This is your auction, so you cannot bid on it.</p>
            ) : auth.status === 'signedIn' ? (
              <BidForm
                listingId={listingId}
                suggested={suggested}
                onPlaced={setMyBid}
              />
            ) : null}
          </div>

          {history.length > 0 && (
            <div className="mt-6 border-t border-slate-100 pt-4">
              <h2 className="text-xs font-medium tracking-wide text-slate-500 uppercase">
                Live updates
              </h2>
              <ul className="mt-2 space-y-1 text-sm tabular-nums">
                {history.map((update) => (
                  <li key={update.version} className="flex justify-between text-slate-600">
                    <span>{formatMoney(update.current_price)}</span>
                    <span className="text-slate-400">#{update.version}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </aside>
      </div>
    </div>
  )
}

function BackLink() {
  return (
    <Link to="/" className="text-sm text-indigo-700 hover:underline">
      ← All auctions
    </Link>
  )
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md bg-white p-3 shadow-sm ring-1 ring-slate-200">
      <dt className="text-slate-500">{label}</dt>
      <dd className="mt-1 font-semibold tabular-nums">{value}</dd>
    </div>
  )
}

const BADGES: Record<Connection, { text: string; style: string }> = {
  connecting: { text: 'Connecting…', style: 'text-slate-500' },
  live: { text: '● Live', style: 'text-green-700' },
  reconnecting: { text: 'Reconnecting…', style: 'text-amber-700' },
  not_found: { text: '', style: '' },
  ended: { text: 'Ended', style: 'text-slate-500' },
}

function ConnectionBadge({ connection }: { connection: Connection }) {
  const badge = BADGES[connection]
  return <span className={`text-xs font-medium ${badge.style}`}>{badge.text}</span>
}
