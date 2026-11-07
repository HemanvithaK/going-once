/** Placing a bid on the bidding service. Shapes mirror services/bidding/app/schemas/bid.py. */

import { services } from '../config'
import type { Session } from './session'

export interface BidResult {
  id: string
  listing_id: string
  max_amount: string // your ceiling: the most you agreed to pay
  current_price: string // the price after your bid
  is_leading: boolean
  created_at: string
}

/**
 * The Idempotency-Key makes the bid safe to send again. If the answer is lost, sending the
 * same key returns the same bid instead of placing a second one. Callers keep one key per
 * attempt and reuse it on retry; the session's own retry after a token refresh reuses it too.
 */
export function placeBid(
  session: Session,
  listingId: string,
  maxAmount: string,
  idempotencyKey: string,
): Promise<BidResult> {
  return session.authorized<BidResult>(`${services.bidding}/listings/${listingId}/bids`, {
    method: 'POST',
    body: JSON.stringify({ max_amount: maxAmount }),
    headers: { 'Idempotency-Key': idempotencyKey },
  })
}
