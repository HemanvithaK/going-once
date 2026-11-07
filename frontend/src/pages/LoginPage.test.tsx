import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { FakeAuthServer, mockFetch } from '../test/fakeApi'
import { renderApp } from '../test/renderApp'

describe('LoginPage', () => {
  it('logs in and shows who is signed in', async () => {
    mockFetch(new FakeAuthServer().handle)
    const user = userEvent.setup()
    const session = renderApp('/login')

    await user.type(screen.getByLabelText('Email'), 'ada@example.com')
    await user.type(screen.getByLabelText('Password'), 'correct horse battery')
    await user.click(screen.getByRole('button', { name: 'Log in' }))

    expect(await screen.findByRole('heading', { name: 'Welcome, Ada' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Log out' })).toBeInTheDocument()
    expect(session.hasRefreshToken).toBe(true)
  })

  it("shows the API's message when the password is wrong", async () => {
    mockFetch(new FakeAuthServer().handle)
    const user = userEvent.setup()
    renderApp('/login')

    await user.type(screen.getByLabelText('Email'), 'ada@example.com')
    await user.type(screen.getByLabelText('Password'), 'wrong password')
    await user.click(screen.getByRole('button', { name: 'Log in' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('invalid email or password')
  })

  it('logging out forgets the stored login', async () => {
    mockFetch(new FakeAuthServer().handle)
    const user = userEvent.setup()
    const session = renderApp('/login')
    await user.type(screen.getByLabelText('Email'), 'ada@example.com')
    await user.type(screen.getByLabelText('Password'), 'correct horse battery')
    await user.click(screen.getByRole('button', { name: 'Log in' }))

    await user.click(await screen.findByRole('button', { name: 'Log out' }))

    expect(await screen.findByRole('link', { name: 'Sign up' })).toBeInTheDocument()
    expect(session.hasRefreshToken).toBe(false)
  })
})
