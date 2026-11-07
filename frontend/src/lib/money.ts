/**
 * Money arithmetic in whole cents. Floating point cannot hold 0.10 exactly, so
 * 0.1 + 0.2 is 0.30000000000000004 in JavaScript. Integers have no such problem.
 */

const AMOUNT = /^(\d{1,10})(?:\.(\d{1,2}))?$/

/** "125.5" -> 12550, or null if the text is not a positive amount with at most two decimals. */
export function parseCents(text: string): number | null {
  const match = AMOUNT.exec(text.trim())
  if (match === null) return null
  const cents = Number(match[1]) * 100 + Number((match[2] ?? '').padEnd(2, '0'))
  return cents > 0 ? cents : null
}

/** 12550 -> "125.50", the format the API expects. */
export function formatCents(cents: number): string {
  return `${Math.floor(cents / 100)}.${String(cents % 100).padStart(2, '0')}`
}

/** "125.00" + "5.00" -> "130.00" */
export function addMoney(a: string, b: string): string {
  const left = parseCents(a)
  const right = parseCents(b)
  if (left === null || right === null) throw new Error(`not money: ${a}, ${b}`)
  return formatCents(left + right)
}
