import { describe, expect, it } from 'vitest'
import { readServiceUrls } from './config'

describe('readServiceUrls', () => {
  it('points at the local docker compose stack when nothing is set', () => {
    expect(readServiceUrls({})).toEqual({
      api: 'http://localhost:8000',
      bidding: 'http://localhost:8001',
      realtime: 'ws://localhost:8002',
    })
  })

  it('uses the environment when it is set, without a trailing slash', () => {
    const urls = readServiceUrls({
      VITE_API_URL: 'https://api.example.com/',
      VITE_BIDDING_URL: 'https://bid.example.com',
      VITE_REALTIME_URL: 'wss://live.example.com//',
    })
    expect(urls).toEqual({
      api: 'https://api.example.com',
      bidding: 'https://bid.example.com',
      realtime: 'wss://live.example.com',
    })
  })
})