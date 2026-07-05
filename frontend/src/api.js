export function getToken() {
  return localStorage.getItem('token')
}

export function setAuth({ token, user_id, name, email }) {
  localStorage.setItem('token', token)
  localStorage.setItem('user', JSON.stringify({ user_id, name, email }))
}

export function getUser() {
  try {
    return JSON.parse(localStorage.getItem('user'))
  } catch {
    return null
  }
}

export function clearAuth() {
  localStorage.removeItem('token')
  localStorage.removeItem('user')
}

export class ApiError extends Error {
  constructor(status, detail) {
    super(detail || `HTTP ${status}`)
    this.status = status
  }
}

export async function api(path, { method = 'GET', body, auth = true } = {}) {
  const headers = { 'Content-Type': 'application/json' }
  if (auth) {
    const token = getToken()
    if (!token) throw new ApiError(401, 'Not logged in')
    headers['Authorization'] = `Bearer ${token}`
  }
  const res = await fetch(path, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
  let data = null
  try {
    data = await res.json()
  } catch {
    /* non-JSON response */
  }
  if (!res.ok) {
    if (res.status === 401 && auth) {
      clearAuth()
      window.location.href = '/'
    }
    throw new ApiError(res.status, data?.detail)
  }
  return data
}
