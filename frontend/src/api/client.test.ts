import { describe, expect, it } from 'vitest'
import { json, mockFetch } from '../test/fakeApi'
import { ApiError, requestJson } from './client'

describe('requestJson', () => {
  it('sends a body as JSON and reads JSON back', async () => {
    const fetch = mockFetch(() => json(200, { ok: true }))

    const result = await requestJson<{ ok: boolean }>('http://api.test/x', {
      method: 'POST',
      body: JSON.stringify({ a: 1 }),
    })

    expect(result).toEqual({ ok: true })
    const init = fetch.mock.calls[0][1] as RequestInit
    expect(new Headers(init.headers).get('Content-Type')).toBe('application/json')
  })

  it("turns the API's error detail into the error message", async () => {
    mockFetch(() => json(409, { detail: 'email already registered' }))

    const error = await requestJson('http://api.test/x').catch((e: unknown) => e)

    expect(error).toBeInstanceOf(ApiError)
    expect(error).toMatchObject({ status: 409, message: 'email already registered' })
  })

  it('names the field when the API rejects the input', async () => {
    mockFetch(() =>
      json(422, {
        detail: [{ loc: ['body', 'display_name'], msg: 'String should have at least 2 characters' }],
      }),
    )

    await expect(requestJson('http://api.test/x')).rejects.toThrow(
      'display name: String should have at least 2 characters',
    )
  })

  it('reports an unreachable server with status 0', async () => {
    mockFetch(() => {
      throw new TypeError('Failed to fetch')
    })

    await expect(requestJson('http://api.test/x')).rejects.toMatchObject({ status: 0 })
  })
})
