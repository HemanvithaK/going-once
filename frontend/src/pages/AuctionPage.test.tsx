import { act, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { FakeAuthServer, ada, bodyOf, json, listing, mockFetch } from '../test/fakeApi'
import { FakeSocket, auctionState } from '../test/fakeSocket'
import { renderApp } from '../test/renderApp'

const ID = '00000000-0000-4000-8000-000000000001'
const camera = listing({ id: ID, title: 'Vintage camera', current_price: '125.00', bid_increment: '5.00' })

interface BidCall {
  key: string | null
  body: Record<string, unknown>
}

/** The core API, the auth endpoints and the bidding service, all faked. */
function backend(options: { bid?: (call: BidCall) => Response; listingOverrides?: object } = {}) {
  const auth = new FakeAuthServer()
  const bids: BidCall[] = []
  const fetch = mockFetch((path, init) => {
    if (path === `/listings/${ID}/bids`) {
      const call = { key: new Headers(init.headers).get('Idempotency-Key'), body: bodyOf(init) }
      bids.push(call)
      return options.bid ? options.bid(call) : json(201, bidResult({}))
    }
    if (path === `/listings/${ID}`) return json(200, { ...camera, ...options.listingOverrides })
    return auth.handle(path, init)
  })
  return { auth, bids, fetch }
}

function bidResult(overrides: object) {
  return {
    id: 'bid-1',
    listing_id: ID,
    max_amount: '150.00',
    current_price: '125.00',
    is_leading: true,
    created_at: '2026-11-06T12:00:00Z',
    ...overrides,
  }
}

function signIn(auth: FakeAuthServer) {
  window.localStorage.setItem('going-once.refresh-token', auth.issue().refresh_token)
}

/** Play the realtime service: send one message on the page's socket. */
function push(state: object) {
  act(() => FakeSocket.latest().send(auctionState({ listing_id: ID, ...state })))
}

async function openAuction() {
  renderApp(`/auctions/${ID}`)
  await screen.findByRole('heading', { name: 'Vintage camera' })
  push({ version: 1, current_price: '125.00' }) // the snapshot
}

async function bid(amount: string) {
  const user = userEvent.setup()
  const input = await screen.findByLabelText('Your maximum bid')
  await user.clear(input)
  await user.type(input, amount)
  await user.click(screen.getByRole('button', { name: 'Place bid' }))
}

beforeEach(() => {
  FakeSocket.reset()
  vi.stubGlobal('WebSocket', FakeSocket)
})

describe('AuctionPage', () => {
  it('shows the live price and follows each update', async () => {
    backend()
    await openAuction()

    expect(FakeSocket.latest().url).toBe(`ws://localhost:8002/ws/listings/${ID}`)
    expect(await screen.findByText('● Live')).toBeInTheDocument()
    push({ version: 2, current_price: '130.00' })

    const panel = screen.getByRole('complementary')
    expect(within(panel).getAllByText('$130.00')[0]).toBeInTheDocument()
    expect(within(panel).getByText('#2')).toBeInTheDocument() // in the live updates list
  })

  it('asks a visitor to log in, then brings them back to the auction', async () => {
    backend()
    const user = userEvent.setup()
    await openAuction()

    await user.click(screen.getByRole('link', { name: 'Log in to bid' }))
    await user.type(screen.getByLabelText('Email'), 'ada@example.com')
    await user.type(screen.getByLabelText('Password'), 'correct horse battery')
    await user.click(screen.getByRole('button', { name: 'Log in' }))

    expect(await screen.findByRole('heading', { name: 'Vintage camera' })).toBeInTheDocument()
  })

  it('places a bid with an idempotency key and says you lead', async () => {
    const { auth, bids } = backend()
    signIn(auth)
    await openAuction()

    expect(await screen.findByLabelText('Your maximum bid')).toHaveValue('130.00') // price + increment
    await bid('150')

    expect(await screen.findByText(/You're the highest bidder, up to your maximum of \$150\.00/)).toBeInTheDocument()
    expect(bids).toHaveLength(1)
    expect(bids[0].body).toEqual({ max_amount: '150.00' })
    expect(bids[0].key).toMatch(/^[0-9a-f-]{36}$/)
  })

  it("shows the bidding service's reason when a bid is refused", async () => {
    const { auth } = backend({ bid: () => json(409, { detail: 'bid must be at least 135.00' }) })
    signIn(auth)
    await openAuction()

    await bid('131')

    expect(await screen.findByRole('alert')).toHaveTextContent('bid must be at least 135.00')
  })

  it('retries with the same key when no answer came back, and a new key for a new amount', async () => {
    let calls = 0
    const { auth, bids } = backend({
      bid: () => {
        calls += 1
        if (calls <= 2) throw new TypeError('Failed to fetch') // the answer was lost, twice
        return json(201, bidResult({ max_amount: '175.00' }))
      },
    })
    signIn(auth)
    await openAuction()

    await bid('150')
    expect(await screen.findByRole('alert')).toHaveTextContent('No answer from the server')
    await bid('150') // same amount, still unanswered: a retry of the same attempt
    await vi.waitFor(() => expect(bids).toHaveLength(2))
    await bid('175') // changed the amount while 150 was unanswered: a new attempt
    await screen.findByText(/You're the highest bidder/)

    expect(bids[1].key).toBe(bids[0].key)
    expect(bids[2].key).not.toBe(bids[0].key)
  })

  it('flags you as outbid once the live price passes your maximum', async () => {
    const { auth } = backend()
    signIn(auth)
    await openAuction()
    await bid('150')
    await screen.findByText(/You're the highest bidder/)

    push({ version: 2, current_price: '155.00' }) // someone else's maximum is higher

    expect(await screen.findByText(/You've been outbid\. Your maximum was \$150\.00/)).toBeInTheDocument()
  })

  it('keeps suggesting the next sensible bid as the price moves', async () => {
    const { auth } = backend()
    signIn(auth)
    await openAuction()
    const input = await screen.findByLabelText('Your maximum bid')
    expect(input).toHaveValue('130.00')

    push({ version: 2, current_price: '140.00' })
    expect(input).toHaveValue('145.00') // follows the price while the user has not typed

    await bid('150')
    await screen.findByText(/You're the highest bidder/)
    // Leading with a maximum of 150: the only bid that makes sense is raising it.
    expect(input).toHaveValue('155.00')

    push({ version: 3, current_price: '155.00' }) // someone beat 150
    expect(input).toHaveValue('160.00') // outbid: beat the new price instead
  })

  it('leaves an amount the user is typing alone when the price moves', async () => {
    const { auth } = backend()
    signIn(auth)
    await openAuction()
    const user = userEvent.setup()
    const input = await screen.findByLabelText('Your maximum bid')
    await user.clear(input)
    await user.type(input, '300')

    push({ version: 2, current_price: '140.00' })

    expect(input).toHaveValue('300')
  })

  it('does not offer the seller a bid form', async () => {
    const { auth } = backend({ listingOverrides: { seller_id: ada.id } })
    signIn(auth)
    await openAuction()

    expect(await screen.findByText(/This is your auction/)).toBeInTheDocument()
    expect(screen.queryByLabelText('Your maximum bid')).not.toBeInTheDocument()
  })

  it('shows the result when the auction is settled, and stops listening', async () => {
    const { auth } = backend()
    signIn(auth)
    await openAuction()

    push({ version: 7, status: 'sold', current_price: '140.00' })

    expect(await screen.findByText('Sold for $140.00')).toBeInTheDocument()
    expect(screen.queryByLabelText('Your maximum bid')).not.toBeInTheDocument()
    expect(FakeSocket.latest().closedByClientWith).toBe(1000)
  })

  it('says so when the auction does not exist', async () => {
    backend()
    renderApp(`/auctions/${ID}`)
    await screen.findByRole('heading', { name: 'Vintage camera' })

    act(() => FakeSocket.latest().drop(4404))

    expect(await screen.findByText(/This auction does not exist/)).toBeInTheDocument()
  })
})
