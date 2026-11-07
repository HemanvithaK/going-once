/**
 * The core API's auth endpoints, one function each. These shapes mirror
 * services/api/app/schemas/auth.py; if the backend changes, change them here.
 */

import { services } from '../config'
import { requestJson, withBearer } from './client'

export interface TokenPair {
  access_token: string
  refresh_token: string
  token_type: string
  expires_in: number // seconds until the access token expires
}

export interface User {
  id: string
  email: string
  display_name: string
  role: string
  created_at: string // ISO 8601 text; JSON has no date type
}

export interface RegisterInput {
  email: string
  password: string
  display_name: string
}

const url = (path: string) => `${services.api}${path}`
const post = (body: unknown): RequestInit => ({ method: 'POST', body: JSON.stringify(body) })

export const authApi = {
  register: (input: RegisterInput) => requestJson<User>(url('/auth/register'), post(input)),
  login: (email: string, password: string) =>
    requestJson<TokenPair>(url('/auth/login'), post({ email, password })),
  refresh: (refreshToken: string) =>
    requestJson<TokenPair>(url('/auth/refresh'), post({ refresh_token: refreshToken })),
  logout: (refreshToken: string) =>
    requestJson<void>(url('/auth/logout'), post({ refresh_token: refreshToken })),
  me: (accessToken: string) => requestJson<User>(url('/auth/me'), withBearer({}, accessToken)),
}
