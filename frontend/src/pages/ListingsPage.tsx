import { useInfiniteQuery } from '@tanstack/react-query'
import { listingsApi } from '../api/listings'
import { ApiError } from '../api/client'
import { ListingCard } from '../components/ListingCard'
import { useNow } from '../lib/useNow'

/**
 * Live auctions, closing soonest first. useInfiniteQuery fetches page after page using
 * the API's cursor, and gives us loading and error states without any hand-written fetch.
 */
export function ListingsPage() {
  const now = useNow()
  const query = useInfiniteQuery({
    queryKey: ['listings', 'live'], // the cache name for this data
    queryFn: ({ pageParam }) => listingsApi.browse(pageParam),
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.next_cursor, // null means "no more pages"
    refetchInterval: 15_000, // prices change; refresh the list every 15 seconds
  })

  if (query.isPending) {
    return <p className="text-slate-500">Loading auctions…</p>
  }
  if (query.isError) {
    const message = query.error instanceof ApiError ? query.error.message : 'something went wrong'
    return (
      <div role="alert" className="rounded-md bg-red-50 p-4 text-red-700">
        <p>Could not load auctions: {message}</p>
        <button
          type="button"
          onClick={() => void query.refetch()}
          className="mt-2 rounded-md border border-red-300 px-3 py-1 text-sm hover:bg-red-100"
        >
          Try again
        </button>
      </div>
    )
  }

  const listings = query.data.pages.flatMap((page) => page.items)

  return (
    <div>
      <h1 className="text-2xl font-bold">Live auctions</h1>
      {listings.length === 0 ? (
        <p className="mt-4 text-slate-600">No live auctions right now. Check back soon.</p>
      ) : (
        <ul className="mt-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {listings.map((listing) => (
            // key tells React which card is which when the list changes
            <li key={listing.id}>
              <ListingCard listing={listing} now={now} />
            </li>
          ))}
        </ul>
      )}
      {query.hasNextPage && (
        <div className="mt-6 text-center">
          <button
            type="button"
            onClick={() => void query.fetchNextPage()}
            disabled={query.isFetchingNextPage}
            className="rounded-md border border-slate-300 bg-white px-4 py-2 text-sm hover:bg-slate-100 disabled:opacity-60"
          >
            {query.isFetchingNextPage ? 'Loading…' : 'Load more'}
          </button>
        </div>
      )}
    </div>
  )
}
