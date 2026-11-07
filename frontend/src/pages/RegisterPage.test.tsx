import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { FakeAuthServer, ada, bodyOf, json, mockFetch } from '../test/fakeApi'
import { renderApp } from '../test/renderApp'

async function fillIn(name: string, email: string, password: string) {
  const user = userEvent.setup()
  await user.type(screen.getByLabelText('Display name'), name)
  await user.type(screen.getByLabelText('Email'), email)
  await user.type(screen.getByLabelText('Password'), password)
  await user.click(screen.getByRole('button', { name: 'Create account' }))
}

describe('RegisterPage', () => {
  it('creates the account, then logs straight in', async () => {
    const server = new FakeAuthServer()
    const fetch = mockFetch((path, init) =>
      path === '/auth/register' ? json(201, ada) : server.handle(path, init),
    )
    renderApp('/register')

    await fillIn('Ada', 'ada@example.com', 'correct horse battery')

    expect(await screen.findByRole('button', { name: 'Log out' })).toBeInTheDocument()
    expect(screen.getByText('Ada')).toBeInTheDocument()
    const paths = fetch.mock.calls.map(([input]) => new URL(String(input)).pathname)
    // Register, then log in, then ask who we are. The auctions page loading after that is fine.
    expect(paths.filter((path) => path.startsWith('/auth/'))).toEqual([
      '/auth/register',
      '/auth/login',
      '/auth/me',
    ])
    expect(bodyOf(fetch.mock.calls[0][1] as RequestInit)).toEqual({
      email: 'ada@example.com',
      password: 'correct horse battery',
      display_name: 'Ada',
    })
  })

  it('refuses a short password without calling the API', async () => {
    const fetch = mockFetch(() => json(500))
    renderApp('/register')

    await fillIn('Ada', 'ada@example.com', 'too short')

    expect(await screen.findByRole('alert')).toHaveTextContent('at least 12 characters')
    expect(fetch).not.toHaveBeenCalled()
  })

  it('shows the conflict when the email is taken', async () => {
    mockFetch(() => json(409, { detail: 'email already registered' }))
    renderApp('/register')

    await fillIn('Ada', 'ada@example.com', 'correct horse battery')

    expect(await screen.findByRole('alert')).toHaveTextContent('email already registered')
  })
})
