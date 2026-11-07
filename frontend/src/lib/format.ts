/**
 * Display helpers. Prices arrive from the API as text ("125.00"), because a JSON number
 * cannot hold money exactly. Number() is fine for showing a price; arithmetic on money
 * never happens here.
 */

const usd = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' })

export function formatMoney(amount: string): string {
  return usd.format(Number(amount))
}

/** "2d 4h", "3h 12m", "4m 05s", "9s", or "ending" once the deadline has passed. */
export function timeLeft(endsAt: string, now: number): string {
  const ms = Date.parse(endsAt) - now
  if (ms <= 0) {
    return 'ending' // the closer settles it within a moment
  }
  const totalSeconds = Math.floor(ms / 1000)
  const days = Math.floor(totalSeconds / 86_400)
  const hours = Math.floor((totalSeconds % 86_400) / 3_600)
  const minutes = Math.floor((totalSeconds % 3_600) / 60)
  const seconds = totalSeconds % 60
  if (days > 0) return `${days}d ${hours}h`
  if (hours > 0) return `${hours}h ${minutes}m`
  if (minutes > 0) return `${minutes}m ${String(seconds).padStart(2, '0')}s`
  return `${seconds}s`
}
