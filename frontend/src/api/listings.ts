/**
 * The core API's listing endpoints. Shapes mirror services/api/app/schemas/listing.py.
 * Money is text ("125.00") and times are ISO 8601 text, exactly as the JSON carries them.
 */

import { services } from '../config'
import { requestJson } from './client'

export type ListingStatus = 'draft' | 'scheduled' | 'live' | 'sold' | 'unsold' | 'cancelled'

export interface Listing {
  id: string
  seller_id: string
  title: string
  description: string
  start_price: string
  bid_increment: string
  current_price: string
  starts_at: string
  ends_at: string
  status: ListingStatus
  created_at: string
  has_reserve: boolean
  reserve_price: string | null // only the seller ever sees the amount
}

export interface ListingPage {
  items: Listing[]
  next_cursor: string | null // null on the last page
}

export const listingsApi = {
  /** Live auctions, closing soonest first. Pass the previous page's cursor for the next page. */
  browse: (cursor: string | null, limit = 12) => {
    const query = new URLSearchParams({ limit: String(limit) })
    if (cursor !== null) query.set('cursor', cursor)
    return requestJson<ListingPage>(`${services.api}/listings?${query}`)
  },

  /** One listing: title, description, prices. Its live price comes from realtime instead. */
  get: (id: string) => requestJson<Listing>(`${services.api}/listings/${id}`),
}
