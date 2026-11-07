import { describe, expect, it } from 'vitest'
import { addMoney, formatCents, parseCents } from './money'

describe('money', () => {
  it('reads amounts with up to two decimals', () => {
    expect(parseCents('125')).toBe(12500)
    expect(parseCents('125.5')).toBe(12550)
    expect(parseCents(' 0.07 ')).toBe(7)
  })

  it('refuses anything that is not a positive amount', () => {
    for (const bad of ['', 'abc', '1.234', '-5', '0', '0.00', '1e3', '12,50']) {
      expect(parseCents(bad)).toBeNull()
    }
  })

  it('adds exactly, where floating point would not', () => {
    expect(0.1 + 0.2).not.toBe(0.3) // the reason this module exists
    expect(addMoney('0.10', '0.20')).toBe('0.30')
    expect(addMoney('125.00', '5.00')).toBe('130.00')
    expect(formatCents(5)).toBe('0.05')
  })
})
