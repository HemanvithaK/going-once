// Adds readable assertions such as expect(element).toBeInTheDocument().
import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach, vi } from 'vitest'

afterEach(() => {
  cleanup() // remove whatever the test drew
  window.localStorage.clear() // no login leaks from one test into the next
  vi.unstubAllGlobals() // undo fetch and navigator stand-ins
})
