/**
 * Holds the signed-in user's tokens and makes authorized calls.
 *
 * The access token lives only in memory. The refresh token is kept in localStorage so a
 * reload keeps you signed in. Script injected into the page could read it; the backend's
 * rotation and reuse detection limit the damage, and an httpOnly cookie would be stronger.
 *
 * The backend treats a refresh token presented twice as stolen and ends every session.
 * So refreshing must never happen twice with the same token:
 *  - within a tab, concurrent callers share one refresh request;
 *  - across tabs, a browser lock lets one tab refresh at a time, and each tab reads the
 *    stored token only once it holds the lock, so it always uses the newest one.
 */

import { authApi, type TokenPair } from './auth'
import { ApiError, requestJson, withBearer } from './client'

const REFRESH_KEY = 'going-once.refresh-token'
const LOCK_NAME = 'going-once.refresh'

async function oneTabAtATime<T>(work: () => Promise<T>): Promise<T> {
  // navigator.locks is in every current browser; the fallback keeps tests and old browsers working.
  if (typeof navigator !== 'undefined' && navigator.locks) {
    return navigator.locks.request(LOCK_NAME, work)
  }
  return work()
}

export class Session {
  private accessToken: string | null = null
  private refreshing: Promise<string | null> | null = null
  private readonly storage: Storage

  constructor(storage: Storage) {
    this.storage = storage
  }

  get hasRefreshToken(): boolean {
    return this.storage.getItem(REFRESH_KEY) !== null
  }

  get refreshToken(): string | null {
    return this.storage.getItem(REFRESH_KEY)
  }

  save(tokens: TokenPair): void {
    this.accessToken = tokens.access_token
    this.storage.setItem(REFRESH_KEY, tokens.refresh_token)
  }

  clear(): void {
    this.accessToken = null
    this.storage.removeItem(REFRESH_KEY)
  }

  /** A new access token from the stored refresh token, or null if the user must log in. */
  refresh(): Promise<string | null> {
    // Everyone who asks while a refresh is running waits for that same one.
    this.refreshing ??= this.refreshOnce().finally(() => {
      this.refreshing = null
    })
    return this.refreshing
  }

  private refreshOnce(): Promise<string | null> {
    return oneTabAtATime(async () => {
      const token = this.storage.getItem(REFRESH_KEY) // read under the lock: the newest one
      if (token === null) {
        this.accessToken = null
        return null
      }
      try {
        const tokens = await authApi.refresh(token)
        this.save(tokens)
        return tokens.access_token
      } catch (error) {
        if (error instanceof ApiError && error.status === 401) {
          this.clear() // expired, revoked or replayed: the user has to log in again
          return null
        }
        throw error // the server is unreachable; keep the token and let the caller retry
      }
    })
  }

  /** Call an endpoint that needs a login. Refreshes once and retries if the token has expired. */
  async authorized<T>(url: string, init: RequestInit = {}): Promise<T> {
    let token = this.accessToken ?? (await this.refresh())
    if (token === null) {
      throw new ApiError(401, 'please log in')
    }
    try {
      return await requestJson<T>(url, withBearer(init, token))
    } catch (error) {
      if (!(error instanceof ApiError) || error.status !== 401) {
        throw error
      }
      // If another call already got a new token while this one was in flight, use it.
      token = this.accessToken !== null && this.accessToken !== token
        ? this.accessToken
        : await this.refresh()
      if (token === null) {
        throw error
      }
      return requestJson<T>(url, withBearer(init, token))
    }
  }
}

export const session = new Session(window.localStorage)
