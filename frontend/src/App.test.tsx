import { screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { FakeAuthServer, mockFetch } from './test/fakeApi'
import { renderApp } from './test/renderApp'

describe('App', () => {
  it('shows the site name and offers to log in when nobody is signed in', async () => {
    renderApp()

    expect(screen.getByRole('link', { name: 'going-once' })).toBeInTheDocument()
    expect(await screen.findByRole('link', { name: 'Sign up' })).toBeInTheDocument()
  })

  it('stays signed in after a reload, using the stored refresh token', async () => {
    const server = new FakeAuthServer()
    mockFetch(server.handle)
    // What a previous visit left behind: only the refresh token survives a reload.
    window.localStorage.setItem('going-once.refresh-token', server.issue().refresh_token)

    renderApp()

    expect(await screen.findByRole('button', { name: 'Log out' })).toBeInTheDocument()
    expect(screen.getByText('Ada')).toBeInTheDocument()
    expect(server.refreshCalls).toHaveLength(1)
  })
})
