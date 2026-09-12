import { useCallback, useEffect, useRef, useState } from 'react'
import * as api from './api'
import { useSessionEvents } from './useSessionEvents'
import ServiceStrip from './components/ServiceStrip'
import ServiceSetup from './components/ServiceSetup'
import UploadPanel from './components/UploadPanel'
import StatusPanel from './components/StatusPanel'
import ChatPanel from './components/ChatPanel'

// The whole app is one state machine:
//
//   connect    point the backend at the three Colab services
//   setup      pick files
//   uploading  PUT each file to storage, one progress bar each
//   processing frozen, asking the API every 2 seconds whether it is done
//   ready      chat
//   failed     something went wrong; show why
//
// Nothing is pushed to us. The backend only writes rows in its database, and
// we find out by asking again. That is polling, and it is how this version
// reports every kind of progress.

// Still here, but only as a safety net. The event stream is what normally
// moves this app forward; polling covers the case where a proxy or a corporate
// network refuses to hold a streaming connection open.
const STATUS_POLL_MS = 15000

export default function App() {
  const [phase, setPhase] = useState('connect')
  const [serviceConfig, setServiceConfig] = useState(null)
  const [checkingServices, setCheckingServices] = useState(true)
  const [sessionId, setSessionId] = useState(null)
  const [status, setStatus] = useState(null)
  const [progress, setProgress] = useState({})
  const [error, setError] = useState(null)
  const timerRef = useRef(null)

  // On load, ask the backend whether it can already reach all three. If it can
  // -- because they were set earlier and the tunnels are still open -- skip
  // straight past the connect screen.
  useEffect(() => {
    let cancelled = false
    api
      .getServiceConfig()
      .then((config) => {
        if (cancelled) return
        setServiceConfig(config)
        if (config.ready) setPhase('setup')
      })
      .catch(() => {
        if (!cancelled) setError('Cannot reach the API on port 8000 — is the monolith running?')
      })
      .finally(() => !cancelled && setCheckingServices(false))
    return () => {
      cancelled = true
    }
  }, [])

  const handleConfigured = useCallback((config) => {
    setServiceConfig(config)
    setError(null)
    if (config.ready) setPhase('setup')
  }, [])

  // "Start again" after a session. Re-check the services first: the Colab
  // runtime may have died while we were chatting, and dropping straight back
  // onto the upload screen would walk past the gate.
  const reset = useCallback(() => {
    clearInterval(timerRef.current)
    setSessionId(null)
    setStatus(null)
    setProgress({})
    setError(null)
    setPhase('connect')
    setCheckingServices(true)
    api
      .getServiceConfig()
      .then((config) => {
        setServiceConfig(config)
        setPhase(config.ready ? 'setup' : 'connect')
      })
      .catch(() => setPhase('connect'))
      .finally(() => setCheckingServices(false))
  }, [])

  /** Go back to the connect screen — the Colab runtime restarted, say. */
  const reconnect = useCallback(() => {
    clearInterval(timerRef.current)
    setPhase('connect')
    setSessionId(null)
    setStatus(null)
    setProgress({})
    setError(null)
    api.getServiceConfig().then(setServiceConfig).catch(() => {})
  }, [])

  // One connection per session, opened as soon as we have an id.
  const stream = useSessionEvents(sessionId)

  // File-level progress, straight from the workers. The fallback poll now runs
  // every 15 seconds, so without this the per-file list would update far less
  // often than it did in version 1 — the events are published, they just have
  // to be merged in.
  useEffect(() => {
    const live = Object.values(stream.files)
    if (live.length === 0) return
    setStatus((prev) => {
      const byId = new Map((prev?.files || []).map((f) => [f.file_id, f]))
      for (const f of live) {
        byId.set(f.file_id, {
          file_id: f.file_id,
          filename: f.filename,
          kind: byId.get(f.file_id)?.kind || '',
          status: f.status,
          chunk_count: f.chunk_count ?? 0,
          error: f.error ?? null,
        })
      }
      return { ...(prev || {}), files: Array.from(byId.values()) }
    })
  }, [stream.files])

  // Session-level progress: the signal to unfreeze the chat box.
  useEffect(() => {
    if (!stream.session) return
    setStatus((prev) => ({ ...(prev || {}), status: stream.session.status,
                           error: stream.session.error }))
    if (stream.session.status === 'ready') setPhase('ready')
    if (stream.session.status === 'failed') {
      setError(stream.session.error || 'processing failed')
      setPhase('failed')
    }
  }, [stream.session])

  // A slow fallback poll, in case the stream cannot be established.
  useEffect(() => {
    if (phase !== 'processing' || !sessionId) return undefined

    const tick = async () => {
      try {
        const fresh = await api.getStatus(sessionId)
        setStatus(fresh)
        if (fresh.status === 'ready') setPhase('ready')
        if (fresh.status === 'failed') {
          setError(fresh.error || 'processing failed')
          setPhase('failed')
        }
      } catch (e) {
        setError(String(e.message || e))
      }
    }

    tick()
    timerRef.current = setInterval(tick, STATUS_POLL_MS)
    return () => clearInterval(timerRef.current)
  }, [phase, sessionId])

  async function handleStart(files) {
    setError(null)
    try {
      const { session_id } = await api.createSession()
      setSessionId(session_id)
      setPhase('uploading')

      const { targets } = await api.presignUploads(session_id, files)

      // Upload everything at once, straight to storage. The API is not
      // involved and never sees a byte.
      await Promise.all(
        targets.map((target, index) =>
          api.uploadToStorage(files[index], target.upload_url, (percent) =>
            setProgress((prev) => ({ ...prev, [target.filename]: percent })),
          ),
        ),
      )

      await api.registerFiles(session_id, targets.map((t) => t.file_id))
      setPhase('processing')
    } catch (e) {
      setError(String(e.message || e))
      setPhase('setup')
    }
  }

  return (
    <div className="app">
      <header className="masthead">
        <p className="eyebrow">Advanced AI Engineering · Version 1</p>
        <h1>EdgentRAG</h1>
        <p className="subtitle">Upload documents and video, then ask questions about them.</p>
      </header>

      <ServiceStrip onReconnect={reconnect} />

      {error && (
        <div className="error">
          <strong>Error</strong>
          <span>{error}</span>
        </div>
      )}

      {phase === 'connect' &&
        (checkingServices ? (
          <section className="card setup-panel">
            <p className="setup-lead">Checking whether the services are reachable…</p>
          </section>
        ) : (
          <ServiceSetup config={serviceConfig} onConfigured={handleConfigured} />
        ))}

      {phase === 'setup' && <UploadPanel onStart={handleStart} />}

      {(phase === 'uploading' || phase === 'processing' || phase === 'failed') && (
        <StatusPanel phase={phase} status={status} progress={progress} onReset={reset} />
      )}

      {phase === 'ready' && (
        <ChatPanel sessionId={sessionId} status={status} onReset={reset} />
      )}
    </div>
  )
}
