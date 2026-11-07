/**
 * One auction's live feed from the realtime service, with reconnects.
 *
 * The first message on a socket is a full snapshot; every later message is the full
 * public state again, with a version that only goes up. So the feed never needs to
 * merge anything: it keeps the newest version it has seen and ignores the rest.
 *
 * Close codes the realtime service sends (services/realtime/app/api/watch.py):
 *   4404  the auction does not exist             -> stop
 *   4008  this viewer fell behind and was dropped -> reconnect at once, a snapshot catches up
 *   1013  try again later (no snapshot, or Redis lost updates) -> reconnect with backoff
 * Anything else (network drop, server restart) also reconnects with backoff.
 */

export type AuctionStatus = 'live' | 'sold' | 'unsold'

export interface AuctionState {
  listing_id: string
  version: number
  status: AuctionStatus
  current_price: string
  ends_at: string
}

export type Connection = 'connecting' | 'live' | 'reconnecting' | 'not_found' | 'ended'

export const CLOSE_NOT_FOUND = 4404
export const CLOSE_TOO_SLOW = 4008
export const CLOSE_TRY_AGAIN = 1013
const CLOSE_NORMAL = 1000

const MAX_BACKOFF_MS = 15_000

interface Listener {
  onState: (state: AuctionState) => void
  onConnection: (connection: Connection) => void
}

/** What the feed needs from the outside world. Tests pass stand-ins. */
export interface FeedDeps {
  createSocket: (url: string) => WebSocket
  random: () => number
}

const browserDeps: FeedDeps = {
  createSocket: (url) => new WebSocket(url),
  random: Math.random,
}

export class AuctionFeed {
  private readonly url: string
  private readonly listener: Listener
  private readonly deps: FeedDeps
  private socket: WebSocket | null = null
  private newestVersion = -1
  private failures = 0
  private retryTimer: ReturnType<typeof setTimeout> | null = null
  private stopped = false

  constructor(url: string, listener: Listener, deps: FeedDeps = browserDeps) {
    this.url = url
    this.listener = listener
    this.deps = deps
  }

  start(): void {
    this.connect()
  }

  /** Close for good: the page went away, or the auction is over. */
  stop(): void {
    this.stopped = true
    if (this.retryTimer !== null) clearTimeout(this.retryTimer)
    this.retryTimer = null
    this.socket?.close(CLOSE_NORMAL)
    this.socket = null
  }

  private connect(): void {
    this.listener.onConnection(this.failures === 0 ? 'connecting' : 'reconnecting')
    const socket = this.deps.createSocket(this.url)
    this.socket = socket
    let snapshotSeen = false

    socket.onmessage = (event: MessageEvent) => {
      const state = parseState(event.data)
      if (state === null) return // not something we understand; the next message will be
      if (!snapshotSeen) {
        snapshotSeen = true
        this.failures = 0 // a snapshot means the connection really works
        this.listener.onConnection('live')
      }
      if (state.version <= this.newestVersion) return // old news: never move the price back
      this.newestVersion = state.version
      this.listener.onState(state)
      if (state.status !== 'live') {
        this.stop() // sold or unsold: nothing will change any more
        this.listener.onConnection('ended')
      }
    }

    socket.onclose = (event: CloseEvent) => {
      if (this.stopped || socket !== this.socket) return // we closed it ourselves
      this.socket = null
      if (event.code === CLOSE_NOT_FOUND) {
        this.stopped = true
        this.listener.onConnection('not_found')
        return
      }
      this.retryAfter(this.delayFor(event.code))
    }
  }

  private delayFor(code: number): number {
    if (code === CLOSE_TOO_SLOW) {
      // We only fell behind; the service is fine. Come back at once, with a little
      // spread so viewers dropped together do not all return in the same millisecond.
      return Math.floor(this.deps.random() * 250)
    }
    // Something is wrong upstream. Wait longer after each failure: 1s, 2s, 4s ... 15s,
    // each scaled by 50-100% at random so a crowd of browsers spreads out.
    const base = Math.min(MAX_BACKOFF_MS, 1000 * 2 ** this.failures)
    return Math.floor(base * (0.5 + this.deps.random() / 2))
  }

  private retryAfter(delayMs: number): void {
    this.failures += 1
    this.listener.onConnection('reconnecting')
    this.retryTimer = setTimeout(() => {
      this.retryTimer = null
      if (!this.stopped) this.connect()
    }, delayMs)
  }
}

const STATUSES: readonly string[] = ['live', 'sold', 'unsold']

/** Accept a message only if it has the fields and types the page relies on. */
function parseState(data: unknown): AuctionState | null {
  if (typeof data !== 'string') return null
  try {
    const value = JSON.parse(data) as Record<string, unknown>
    if (
      typeof value.listing_id === 'string' &&
      typeof value.version === 'number' &&
      typeof value.current_price === 'string' &&
      typeof value.ends_at === 'string' &&
      typeof value.status === 'string' &&
      STATUSES.includes(value.status)
    ) {
      return {
        listing_id: value.listing_id,
        version: value.version,
        status: value.status as AuctionStatus,
        current_price: value.current_price,
        ends_at: value.ends_at,
      }
    }
  } catch {
    // not JSON
  }
  return null
}
