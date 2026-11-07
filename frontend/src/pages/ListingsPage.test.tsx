import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { json, listing, mockFetch } from '../test/fakeApi'
import { renderApp } from '../test/renderApp'

describe('ListingsPage', () => {
  it('shows each live auction with its price, time left and reserve', async () => {
    // Fixed before the page starts its clock, with half a minute of slack either way.
    const endsAt = new Date(Date.now() + (2 * 3_600 + 30 * 60 + 30) * 1000).toISOString()
    const items = [
      listing({ title: 'Vintage camera', current_price: '125.00', has_reserve: true, ends_at: endsAt }),
      listing({ title: 'Oak desk', current_price: '1240.50', ends_at: endsAt }),
    ]
    mockFetch(() => json(200, { items, next_cursor: null }))
    renderApp('/')

    expect(await screen.findByText('Vintage camera')).toBeInTheDocument()
    expect(screen.getByText('$125.00')).toBeInTheDocument()
    expect(screen.getByText('$1,240.50')).toBeInTheDocument()
    expect(screen.getByText('Reserve')).toBeInTheDocument()
    expect(screen.getByText('No reserve')).toBeInTheDocument()
    expect(screen.getAllByText('Ends in 2h 30m')).toHaveLength(2)
  })

  it('links each auction to its own page', async () => {
    const camera = listing({ title: 'Vintage camera' })
    mockFetch(() => json(200, { items: [camera], next_cursor: null }))
    renderApp('/')

    const link = await screen.findByRole('link', { name: /Vintage camera/ })
    expect(link).toHaveAttribute('href', `/auctions/${camera.id}`)
  })

  it('loads the next page with the cursor from the last one', async () => {
    const requested: (string | null)[] = []
    const pages: Record<string, unknown> = {
      first: { items: [listing({ title: 'First page item' })], next_cursor: 'abc123' },
      abc123: { items: [listing({ title: 'Second page item' })], next_cursor: null },
    }
    const fetchMock = mockFetch(() => json(500))
    // Answer by cursor, the way the API pages through results.
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const cursor = new URL(String(input)).searchParams.get('cursor')
      requested.push(cursor)
      return json(200, pages[cursor ?? 'first'])
    })
    const user = userEvent.setup()
    renderApp('/')

    expect(await screen.findByText('First page item')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Load more' }))

    expect(await screen.findByText('Second page item')).toBeInTheDocument()
    expect(screen.getByText('First page item')).toBeInTheDocument() // kept, not replaced
    expect(requested).toEqual([null, 'abc123'])
    expect(screen.queryByRole('button', { name: 'Load more' })).not.toBeInTheDocument()
  })

  it('says so when nothing is live', async () => {
    mockFetch(() => json(200, { items: [], next_cursor: null }))
    renderApp('/')

    expect(await screen.findByText(/No live auctions right now/)).toBeInTheDocument()
  })

  it('shows the error and lets the user try again', async () => {
    let calls = 0
    mockFetch(() => {
      calls += 1
      return calls === 1
        ? json(503, { detail: 'database unavailable' })
        : json(200, { items: [listing({ title: 'Back online' })], next_cursor: null })
    })
    const user = userEvent.setup()
    renderApp('/')

    expect(await screen.findByRole('alert')).toHaveTextContent('database unavailable')
    await user.click(screen.getByRole('button', { name: 'Try again' }))

    expect(await screen.findByText('Back online')).toBeInTheDocument()
  })
})
