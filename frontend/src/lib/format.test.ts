import { describe, expect, it } from 'vitest'
import { formatMoney, timeLeft } from './format'

describe('formatMoney', () => {
  it('shows dollars with cents and thousands separators', () => {
    expect(formatMoney('125.00')).toBe('$125.00')
    expect(formatMoney('1234.5')).toBe('$1,234.50')
  })
})

describe('timeLeft', () => {
  const now = Date.parse('2026-11-06T12:00:00Z')
  const at = (seconds: number) => new Date(now + seconds * 1000).toISOString()

  it('picks the two largest units that matter', () => {
    expect(timeLeft(at(2 * 86_400 + 4 * 3_600 + 30), now)).toBe('2d 4h')
    expect(timeLeft(at(3 * 3_600 + 12 * 60 + 5), now)).toBe('3h 12m')
    expect(timeLeft(at(4 * 60 + 5), now)).toBe('4m 05s')
    expect(timeLeft(at(9), now)).toBe('9s')
  })

  it('says "ending" once the deadline has passed', () => {
    expect(timeLeft(at(0), now)).toBe('ending')
    expect(timeLeft(at(-30), now)).toBe('ending')
  })
})
