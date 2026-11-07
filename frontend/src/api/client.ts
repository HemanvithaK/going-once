/**
 * The one place that talks HTTP. Every call goes through requestJson, so errors look the
 * same everywhere: an ApiError with the HTTP status and a message a person can read.
 */

export class ApiError extends Error {
  readonly status: number // 0 means the server could not be reached at all

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

// FastAPI sends {"detail": "..."} for errors it raises, and a list for invalid input.
interface ValidationIssue {
  loc?: (string | number)[]
  msg: string
}
interface ErrorBody {
  detail?: string | ValidationIssue[]
}

export async function errorFrom(response: Response): Promise<ApiError> {
  let message = `request failed (${response.status})`
  try {
    const body = (await response.json()) as ErrorBody
    if (typeof body.detail === 'string') {
      message = body.detail
    } else if (Array.isArray(body.detail) && body.detail.length > 0) {
      message = body.detail.map(describeIssue).join('; ')
    }
  } catch {
    // The body was not JSON. Keep the generic message.
  }
  return new ApiError(response.status, message)
}

function describeIssue(issue: ValidationIssue): string {
  // loc looks like ["body", "password"]; the last part names the field.
  const field = issue.loc?.at(-1)
  return typeof field === 'string' ? `${field.replace(/_/g, ' ')}: ${issue.msg}` : issue.msg
}

/** Send a request and read JSON back. Throws ApiError for anything but a 2xx answer. */
export async function requestJson<T>(url: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  if (init.body !== undefined && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }

  let response: Response
  try {
    response = await fetch(url, { ...init, headers })
  } catch {
    // fetch only throws when there is no answer at all: server down, network gone, CORS refused.
    throw new ApiError(0, 'could not reach the server')
  }

  if (!response.ok) {
    throw await errorFrom(response)
  }
  if (response.status === 204) {
    return undefined as T
  }
  return (await response.json()) as T
}

export function withBearer(init: RequestInit, token: string): RequestInit {
  const headers = new Headers(init.headers)
  headers.set('Authorization', `Bearer ${token}`)
  return { ...init, headers }
}
