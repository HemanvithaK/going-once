import { describe, expect, it, vi } from 'vitest'
import { FakeAuthServer, fakeLocks, mockFetch } from '../test/fakeApi'
import { ApiError } from './client'
import { Session } from './session'

const ME = 'http://localhost:8000/auth/me'

function signedInSession(server: FakeAuthServer): Session {
  const session = new Session(window.localStorage)
  session.save(server.issue())
  return session
}

describe('Session', () => {
  it('shares one refresh between calls that all find the access token expired', async () => {
    const server = new FakeAuthServer()
    mockFetch(server.handle)
    const session = signedInSession(server)
    server.expireAccessTokens()

    const users = await Promise.all([
      session.authorized(ME),
      session.authorized(ME),
      session.authorized(ME),
    ])

    expect(users).toHaveLength(3)
    expect(server.refreshCalls).toHaveLength(1) // one refresh, not three
    expect(server.reuseDetected).toBe(false)
  })

  it('never presents the same refresh token twice when two tabs refresh at once', async () => {
    const server = new FakeAuthServer()
    mockFetch(server.handle)
    vi.stubGlobal('navigator', { ...navigator, locks: fakeLocks() })
    window.localStorage.setItem('going-once.refresh-token', server.issue().refresh_token)
    // Two tabs: separate sessions, one shared localStorage.
    const tabA = new Session(window.localStorage)
    const tabB = new Session(window.localStorage)

    const [a, b] = await Promise.all([tabA.refresh(), tabB.refresh()])

    expect(a).not.toBeNull()
    expect(b).not.toBeNull()
    expect(server.reuseDetected).toBe(false)
    expect(new Set(server.refreshCalls).size).toBe(server.refreshCalls.length)
  })

  it('signs out when the server rejects the refresh token', async () => {
    const server = new FakeAuthServer()
    mockFetch(server.handle)
    window.localStorage.setItem('going-once.refresh-token', 'revoked-long-ago')
    const session = new Session(window.localStorage)

    await expect(session.authorized(ME)).rejects.toMatchObject({ status: 401 })

    expect(session.hasRefreshToken).toBe(false)
  })

  it('keeps the refresh token when the server cannot be reached', async () => {
    mockFetch(() => {
      throw new TypeError('Failed to fetch')
    })
    window.localStorage.setItem('going-once.refresh-token', 'r1')
    const session = new Session(window.localStorage)

    const error = await session.refresh().catch((e: unknown) => e)

    expect(error).toBeInstanceOf(ApiError)
    expect(session.hasRefreshToken).toBe(true) // an outage is not a reason to log out
  })
})
