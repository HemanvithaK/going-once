import { vi } from 'vitest'

/** A JSON response, the way the API would send it. */
export function json(status: number, body?: unknown): Response {
  return new Response(body === undefined ? null : JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

type Handler = (path: string, init: RequestInit) => Response | Promise<Response>

/** Replace fetch for one test. The handler sees the path (no host) and the request. */
export function mockFetch(handler: Handler) {
  const fn = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) =>
    handler(new URL(String(input)).pathname, init),
  )
  vi.stubGlobal('fetch', fn)
  return fn
}

export function bearerOf(init: RequestInit): string | null {
  return new Headers(init.headers).get('Authorization')
}

export function bodyOf(init: RequestInit): Record<string, unknown> {
  return JSON.parse(String(init.body)) as Record<string, unknown>
}

export const ada = {
  id: '6f1c2b8e-0000-4000-8000-000000000001',
  email: 'ada@example.com',
  display_name: 'Ada',
  role: 'user',
  created_at: '2026-11-06T10:00:00Z',
}

/**
 * The core API's auth endpoints in memory, with the same rules as the real ones:
 * refresh tokens rotate, and presenting a spent one counts as theft.
 */
export class FakeAuthServer {
  private next = 1
  private readonly live = new Set<string>()
  private readonly spent = new Set<string>()
  private readonly accessTokens = new Set<string>()
  reuseDetected = false
  refreshCalls: string[] = []

  issue(): { access_token: string; refresh_token: string; token_type: string; expires_in: number } {
    const n = this.next++
    this.live.add(`r${n}`)
    this.accessTokens.add(`a${n}`)
    return { access_token: `a${n}`, refresh_token: `r${n}`, token_type: 'bearer', expires_in: 900 }
  }

  expireAccessTokens(): void {
    this.accessTokens.clear()
  }

  handle = (path: string, init: RequestInit): Response => {
    if (path === '/auth/login') {
      const { password } = bodyOf(init)
      return password === 'correct horse battery'
        ? json(200, this.issue())
        : json(401, { detail: 'invalid email or password' })
    }
    if (path === '/auth/refresh') {
      const token = String(bodyOf(init).refresh_token)
      this.refreshCalls.push(token)
      if (this.spent.has(token)) {
        this.reuseDetected = true
        return json(401, { detail: 'invalid or expired refresh token' })
      }
      if (!this.live.delete(token)) {
        return json(401, { detail: 'invalid or expired refresh token' })
      }
      this.spent.add(token)
      return json(200, this.issue())
    }
    if (path === '/auth/me') {
      const token = bearerOf(init)?.replace('Bearer ', '') ?? ''
      return this.accessTokens.has(token) ? json(200, ada) : json(401, { detail: 'not authenticated' })
    }
    if (path === '/listings') {
      return json(200, { items: [], next_cursor: null })
    }
    if (path === '/auth/logout') {
      return new Response(null, { status: 204 })
    }
    return json(404, { detail: 'not found' })
  }
}

/** A stand-in for the browser's lock manager: one holder at a time, in arrival order. */
export function fakeLocks() {
  let queue: Promise<unknown> = Promise.resolve()
  return {
    request<T>(_name: string, work: () => Promise<T>): Promise<T> {
      const run = queue.then(work)
      queue = run.catch(() => undefined)
      return run
    },
  }
}

/** A live listing as the API would send it. Override any field. */
export function listing(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    id: crypto.randomUUID(),
    seller_id: '6f1c2b8e-0000-4000-8000-000000000009',
    title: 'Vintage camera',
    description: '',
    start_price: '100.00',
    bid_increment: '5.00',
    current_price: '125.00',
    starts_at: '2026-11-06T10:00:00Z',
    ends_at: new Date(Date.now() + 3 * 3_600_000).toISOString(),
    status: 'live',
    created_at: '2026-11-06T10:00:00Z',
    has_reserve: false,
    reserve_price: null,
    ...overrides,
  }
}
