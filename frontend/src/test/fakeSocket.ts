/**
 * A stand-in for the browser's WebSocket. Tests play the server: they push messages and
 * close the connection with any code, then check what the page or the feed did.
 */
export class FakeSocket {
  static all: FakeSocket[] = []
  readonly url: string
  onmessage: ((event: MessageEvent) => void) | null = null
  onclose: ((event: CloseEvent) => void) | null = null
  closedByClientWith: number | null = null

  constructor(url: string) {
    this.url = url
    FakeSocket.all.push(this)
  }

  static latest(): FakeSocket {
    const socket = FakeSocket.all.at(-1)
    if (socket === undefined) throw new Error('no socket was opened')
    return socket
  }

  static reset(): void {
    FakeSocket.all = []
  }

  /** The client closing it, as the browser API does. */
  close(code?: number): void {
    this.closedByClientWith = code ?? 1000
  }

  /** The server sending one message. */
  send(state: object): void {
    this.onmessage?.({ data: JSON.stringify(state) } as MessageEvent)
  }

  /** The server (or the network) closing the connection. */
  drop(code: number): void {
    this.onclose?.({ code } as CloseEvent)
  }
}

export function auctionState(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    type: 'auction.updated',
    schema_version: 1,
    listing_id: '00000000-0000-4000-8000-000000000001',
    version: 1,
    status: 'live',
    current_price: '125.00',
    ends_at: new Date(Date.now() + 3_600_000).toISOString(),
    ...overrides,
  }
}
