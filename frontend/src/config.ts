/**
 * Where the three backend services live.
 *
 * Vite only exposes environment variables that start with VITE_, and it bakes them into
 * the bundle at build time. Nothing secret may ever go here: anyone can read the bundle.
 */
export interface ServiceUrls {
  api: string // users, listings, orders
  bidding: string // placing bids
  realtime: string // the live WebSocket feed
}

type Env = Record<string, string | undefined>

export function readServiceUrls(env: Env): ServiceUrls {
  return {
    api: withoutTrailingSlash(env.VITE_API_URL ?? 'http://localhost:8000'),
    bidding: withoutTrailingSlash(env.VITE_BIDDING_URL ?? 'http://localhost:8001'),
    realtime: withoutTrailingSlash(env.VITE_REALTIME_URL ?? 'ws://localhost:8002'),
  }
}

function withoutTrailingSlash(url: string): string {
  return url.replace(/\/+$/, '')
}

export const services: ServiceUrls = readServiceUrls(import.meta.env)