import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { FakeSocket, auctionState } from '../test/fakeSocket'
import {
  AuctionFeed,
  CLOSE_NOT_FOUND,
  CLOSE_TOO_SLOW,
  CLOSE_TRY_AGAIN,
  type AuctionState,
  type Connection,
} from './auctionFeed'

function startFeed(random = () => 1) {
  const states: AuctionState[] = []
  const connections: Connection[] = []
  const feed = new AuctionFeed(
    'ws://realtime.test/ws/listings/x',
    { onState: (s) => states.push(s), onConnection: (c) => connections.push(c) },
    { createSocket: (url) => new FakeSocket(url) as unknown as WebSocket, random },
  )
  feed.start()
  return { feed, states, connections, prices: () => states.map((s) => s.current_price) }
}

beforeEach(() => {
  FakeSocket.reset()
  vi.useFakeTimers() // backoff delays run on a fake clock we move by hand
})
afterEach(() => {
  vi.useRealTimers()
})

describe('AuctionFeed', () => {
  it('shows the snapshot, then only newer versions', () => {
    const { connections, prices } = startFeed()
    const socket = FakeSocket.latest()

    socket.send(auctionState({ version: 3, current_price: '125.00' }))
    socket.send(auctionState({ version: 4, current_price: '130.00' }))
    socket.send(auctionState({ version: 2, current_price: '110.00' })) // late and stale
    socket.send(auctionState({ version: 4, current_price: '130.00' })) // a repeat

    expect(prices()).toEqual(['125.00', '130.00'])
    expect(connections).toEqual(['connecting', 'live'])
  })

  it('ignores messages it cannot read instead of breaking', () => {
    const { prices } = startFeed()
    const socket = FakeSocket.latest()

    socket.onmessage?.({ data: 'not json' } as MessageEvent)
    socket.send({ version: 'one' })
    socket.send(auctionState({ version: 1 }))

    expect(prices()).toEqual(['125.00'])
  })

  it('comes straight back after being dropped for falling behind', () => {
    const { connections, prices } = startFeed()
    FakeSocket.latest().send(auctionState({ version: 1, current_price: '125.00' }))

    FakeSocket.latest().drop(CLOSE_TOO_SLOW)
    vi.advanceTimersByTime(250)
    expect(FakeSocket.all).toHaveLength(2)

    FakeSocket.latest().send(auctionState({ version: 9, current_price: '170.00' })) // catches up
    expect(prices()).toEqual(['125.00', '170.00'])
    expect(connections).toEqual(['connecting', 'live', 'reconnecting', 'reconnecting', 'live'])
  })

  it('waits longer after each failure when the service is unavailable', () => {
    startFeed() // random() = 1, so each wait is exactly its base: 1s, 2s, 4s
    for (const wait of [1000, 2000, 4000]) {
      const before = FakeSocket.all.length
      FakeSocket.latest().drop(CLOSE_TRY_AGAIN)
      vi.advanceTimersByTime(wait - 1)
      expect(FakeSocket.all).toHaveLength(before) // not yet
      vi.advanceTimersByTime(1)
      expect(FakeSocket.all).toHaveLength(before + 1)
    }
  })

  it('never waits more than 15 seconds between attempts', () => {
    startFeed()
    for (let i = 0; i < 10; i++) {
      FakeSocket.latest().drop(1006) // network gone
      vi.advanceTimersByTime(15_000)
    }
    expect(FakeSocket.all).toHaveLength(11)
  })

  it('starts the wait over once a connection works again', () => {
    startFeed()
    FakeSocket.latest().drop(CLOSE_TRY_AGAIN)
    vi.advanceTimersByTime(1000)
    FakeSocket.latest().drop(CLOSE_TRY_AGAIN)
    vi.advanceTimersByTime(2000)
    FakeSocket.latest().send(auctionState()) // a snapshot: the service is back

    FakeSocket.latest().drop(CLOSE_TRY_AGAIN)
    vi.advanceTimersByTime(1000) // back to a 1 second wait, not 4
    expect(FakeSocket.all).toHaveLength(4)
  })

  it('stops for good when the auction does not exist', () => {
    const { connections } = startFeed()

    FakeSocket.latest().drop(CLOSE_NOT_FOUND)
    vi.advanceTimersByTime(60_000)

    expect(FakeSocket.all).toHaveLength(1)
    expect(connections.at(-1)).toBe('not_found')
  })

  it('closes once the auction is settled', () => {
    const { connections } = startFeed()
    const socket = FakeSocket.latest()

    socket.send(auctionState({ version: 5, status: 'sold', current_price: '140.00' }))

    expect(socket.closedByClientWith).toBe(1000)
    expect(connections.at(-1)).toBe('ended')
    socket.drop(1000) // the close echo must not trigger a reconnect
    vi.advanceTimersByTime(60_000)
    expect(FakeSocket.all).toHaveLength(1)
  })

  it('stop() closes the socket and cancels any pending reconnect', () => {
    const { feed } = startFeed()
    FakeSocket.latest().drop(CLOSE_TRY_AGAIN) // a reconnect is now scheduled

    feed.stop()
    vi.advanceTimersByTime(60_000)

    expect(FakeSocket.all).toHaveLength(1)
  })
})
