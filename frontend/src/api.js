// Thin API client for the FindIt backend.
//
// Every failure mode is surfaced as an ApiError with a readable message, so no
// caller can accidentally swallow one. The dashboard's acceptance criterion is
// that the locate UI never silently hides an error.

const BASE_URL = (import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000').replace(/\/$/, '')
const TIMEOUT_MS = 8000

export class ApiError extends Error {
  constructor(message, { status = 0, kind = 'http', detail = null } = {}) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.kind = kind // 'http' | 'network' | 'timeout' | 'parse'
    this.detail = detail
  }
}

async function request(path, { method = 'GET', body = null } = {}) {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS)

  let response
  try {
    response = await fetch(`${BASE_URL}${path}`, {
      method,
      signal: controller.signal,
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    })
  } catch (err) {
    clearTimeout(timer)
    if (err.name === 'AbortError') {
      throw new ApiError(`The backend did not answer within ${TIMEOUT_MS / 1000}s.`, {
        kind: 'timeout',
      })
    }
    throw new ApiError(`Cannot reach the backend at ${BASE_URL}. Is uvicorn running?`, {
      kind: 'network',
    })
  }
  clearTimeout(timer)

  const text = await response.text()
  let payload = null
  if (text) {
    try {
      payload = JSON.parse(text)
    } catch {
      throw new ApiError('The backend returned a response that is not JSON.', {
        status: response.status,
        kind: 'parse',
      })
    }
  }

  if (!response.ok) {
    const detail = payload && payload.detail ? payload.detail : null
    throw new ApiError(detailMessage(response.status, detail), {
      status: response.status,
      detail,
    })
  }
  return payload
}

function detailMessage(status, detail) {
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail) && detail.length && detail[0].msg) {
    // FastAPI validation errors arrive as a list of objects.
    return detail.map((d) => d.msg).join('; ')
  }
  return `Request failed with HTTP ${status}.`
}

export const api = {
  baseUrl: BASE_URL,
  health: () => request('/health'),
  architecture: () => request('/api/architecture'),
  controllers: () => request('/api/controllers'),
  drawerMap: () => request('/api/drawers'),
  search: (q) => request(`/api/search?q=${encodeURIComponent(q)}`),
  locate: (payload) => request('/api/locate', { method: 'POST', body: payload }),
  command: (id) => request(`/api/commands/${encodeURIComponent(id)}`),
}

export default api
