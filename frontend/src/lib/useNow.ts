import { useEffect, useState } from 'react'

/**
 * The current time, updated every interval. A component that calls this re-draws on each
 * tick, so countdowns move. Call it once per page and pass the value down, rather than
 * running one timer per card.
 */
export function useNow(intervalMs = 1000): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), intervalMs)
    return () => clearInterval(id) // stop the timer when the component goes away
  }, [intervalMs])
  return now
}
