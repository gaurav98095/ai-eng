import { useEffect, useRef, useState } from 'react'

// One connection per session, replacing the two-second poll.
//
// Version 1 asked "is it done yet?" every two seconds. With three hundred users
// that is a hundred and fifty requests a second carrying no information most of
// the time, and the user still waited up to two seconds after the answer
// already existed. Here the server writes when it has something to say.
//
// EventSource rather than a WebSocket because the traffic is one-directional --
// the browser has nothing to say mid-answer -- and because the browser
// reconnects on its own if the connection drops.

const BASE = (import.meta.env.VITE_API_BASE || 'http://localhost:8000').replace(/\/$/, '')

/**
 * Subscribe to everything happening in one session.
 *
 * Returns { connected, files, session, tokens } where `tokens` accumulates
 * answer fragments keyed by message id — so a streaming answer can be rendered
 * as it arrives rather than when it is finished.
 */
export function useSessionEvents(sessionId) {
  const [connected, setConnected] = useState(false)
  const [files, setFiles] = useState({})
  const [session, setSession] = useState(null)
  const [tokens, setTokens] = useState({})
  const sourceRef = useRef(null)

  useEffect(() => {
    if (!sessionId) return undefined

    const source = new EventSource(`${BASE}/sessions/${sessionId}/events`)
    sourceRef.current = source

    source.addEventListener('open', () => setConnected(true))
    source.onopen = () => setConnected(true)

    // A file changed status.
    source.addEventListener('file', (e) => {
      const d = JSON.parse(e.data)
      setFiles((prev) => ({ ...prev, [d.file_id]: d }))
    })

    // The session changed status — the signal to unfreeze the chat box.
    source.addEventListener('session', (e) => setSession(JSON.parse(e.data)))

    // A message changed status: retrieving, generating, done, failed.
    source.addEventListener('message', (e) => {
      const d = JSON.parse(e.data)
      setTokens((prev) => ({
        ...prev,
        [d.message_id]: { ...(prev[d.message_id] || { text: '' }), status: d.status, stage: d.stage },
      }))
    })

    // A fragment of an answer. Appended, so this already works token by token
    // the moment the model service can stream.
    source.addEventListener('token', (e) => {
      const d = JSON.parse(e.data)
      setTokens((prev) => {
        const existing = prev[d.message_id] || { text: '', status: 'answering' }
        return { ...prev, [d.message_id]: { ...existing, text: existing.text + d.text } }
      })
    })

    // The browser retries on its own; this only reflects it in the UI.
    source.onerror = () => setConnected(false)

    return () => {
      source.close()
      sourceRef.current = null
      setConnected(false)
    }
  }, [sessionId])

  return { connected, files, session, tokens }
}
