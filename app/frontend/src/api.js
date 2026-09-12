// Every call the frontend makes to the API.
//
// VITE_API_BASE is /api in a production build (see frontend/.env.production),
// so the browser talks to the same origin it was served from and nginx
// forwards it. Same origin means CORS never comes into it.

const BASE = (import.meta.env.VITE_API_BASE || 'http://localhost:8000').replace(/\/$/, '')

async function request(path, options = {}) {
  const response = await fetch(`${BASE}${path}`, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
  })

  if (!response.ok) {
    let detail = await response.text()
    try {
      detail = JSON.parse(detail).detail || detail
    } catch {
      /* not JSON; use the raw body */
    }
    throw new Error(`${response.status} — ${detail}`)
  }

  return response.status === 204 ? null : response.json()
}

/** Which of the five processes are actually running. */
export function getHealth() {
  return request('/health')
}

/**
 * Where the three GPU services currently live, and whether they answer.
 *
 * They run on Colab now, behind tunnels whose addresses change every time the
 * runtime restarts — so they are configured here rather than in the backend's
 * .env. Returns { urls, healthy, configured, ready }.
 */
export function getServiceConfig() {
  return request('/config/services')
}

/**
 * Point the backend at three new addresses.
 *
 * The backend checks all three answer /health before it saves, so a rejected
 * call means one of the URLs is wrong or its tunnel has closed.
 */
export function setServiceConfig(urls) {
  return request('/config/services', {
    method: 'PUT',
    body: JSON.stringify(urls),
  })
}

export function createSession() {
  return request('/sessions', { method: 'POST' })
}

/** Ask for one upload URL per file. Returns { targets: [...] }. */
export function presignUploads(sessionId, files) {
  return request(`/sessions/${sessionId}/uploads`, {
    method: 'POST',
    body: JSON.stringify({
      files: files.map((file) => ({
        filename: file.name,
        content_type: file.type || 'application/octet-stream',
        size: file.size,
      })),
    }),
  })
}

/**
 * PUT one file straight to storage.
 *
 * XHR rather than fetch, because fetch cannot report upload progress.
 *
 * The URL is a presigned S3 link from the backend. The bytes go straight to
 * the bucket and never touch the API, which is why a two-gigabyte video does
 * not need a two-gigabyte server.
 */
export function uploadToStorage(file, url, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('PUT', url)
    xhr.setRequestHeader('Content-Type', file.type || 'application/octet-stream')

    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress(Math.round((event.loaded / event.total) * 100))
    }
    xhr.onload = () =>
      xhr.status >= 200 && xhr.status < 300
        ? resolve()
        : reject(new Error(`upload failed with ${xhr.status}`))
    xhr.onerror = () => reject(new Error('upload failed — is the API running?'))

    xhr.send(file)
  })
}

/** Tell the backend the uploads are done, and start processing. */
export function registerFiles(sessionId, fileIds) {
  return request(`/sessions/${sessionId}/files/register`, {
    method: 'POST',
    body: JSON.stringify({ files: fileIds.map((file_id) => ({ file_id })) }),
  })
}

export function getStatus(sessionId) {
  return request(`/sessions/${sessionId}/status`)
}

export function postChat(sessionId, content) {
  return request(`/sessions/${sessionId}/chat`, {
    method: 'POST',
    body: JSON.stringify({ content }),
  })
}

/**
 * The event stream for a session.
 *
 * Progress and answers arrive here instead of being polled for. See
 * useSessionEvents.js — this is exported so a component can open its own
 * EventSource if it needs one.
 */
export function eventsUrl(sessionId) {
  return `${BASE}/sessions/${sessionId}/events`
}

export function getMessages(sessionId) {
  return request(`/sessions/${sessionId}/chat`)
}
