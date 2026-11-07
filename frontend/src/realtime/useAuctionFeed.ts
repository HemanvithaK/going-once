import { useEffect, useState } from 'react'
import { services } from '../config'
import { AuctionFeed, type AuctionState, type Connection } from './auctionFeed'

const HISTORY_LENGTH = 8

/**
 * The live state of one auction. Opens the feed when the component appears and closes
 * it when it goes away. The component using this must be keyed by listingId, so a new
 * auction always starts from empty state.
 */
export function useAuctionFeed(listingId: string) {
  const [state, setState] = useState<AuctionState | null>(null)
  const [connection, setConnection] = useState<Connection>('connecting')
  const [history, setHistory] = useState<AuctionState[]>([])

  useEffect(() => {
    const feed = new AuctionFeed(`${services.realtime}/ws/listings/${listingId}`, {
      onState: (next) => {
        setState(next)
        setHistory((older) => [next, ...older].slice(0, HISTORY_LENGTH))
      },
      onConnection: setConnection,
    })
    feed.start()
    return () => feed.stop()
  }, [listingId])

  return { state, connection, history }
}
