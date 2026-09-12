import { useEffect, useState } from 'react'
import * as api from '../api'

// Five processes have to be running for this system to work, and when one of
// them is not, the symptom usually shows up somewhere else entirely. This strip
// asks the API which services it can reach, so you can see the real problem
// instead of guessing.
//
// Three of the five are on Colab now, so a dot going red here usually means the
// runtime was recycled and the tunnels are gone — not that anything is broken.

const LABELS = { embedding: 'embedding', stt: 'stt', llm: 'llm' }

/** trycloudflare URLs are long and all look alike; the host is the useful bit. */
function hostOf(url) {
  try {
    return new URL(url).host
  } catch {
    return url
  }
}

export default function ServiceStrip({ onReconnect }) {
  const [health, setHealth] = useState(null)
  const [reachable, setReachable] = useState(true)

  useEffect(() => {
    const check = async () => {
      try {
        setHealth(await api.getHealth())
        setReachable(true)
      } catch {
        setReachable(false)
      }
    }
    check()
    const timer = setInterval(check, 10000)
    return () => clearInterval(timer)
  }, [])

  if (!reachable) {
    return (
      <div className="strip">
        <span className="dot bad" />
        <span>API not reachable on port 8000 — is the monolith running?</span>
      </div>
    )
  }

  if (!health) return null

  const anyDown = Object.values(health.services || {}).some((ok) => !ok)
  // Always offer the way back, not only when something is red: you may want to
  // point at a different Colab instance while this one is still perfectly fine.

  return (
    <div className="strip">
      <span className="dot good" />
      <span className="strip-label">api · 8000</span>

      {Object.entries(LABELS).map(([key, label]) => (
        <span
          key={key}
          className="strip-item"
          title={health.service_urls?.[key] || ''}
        >
          <span className={`dot ${health.services?.[key] ? 'good' : 'bad'}`} />
          <span className="strip-label">
            {label}
            {health.service_urls?.[key] && (
              <span className="strip-host">{hostOf(health.service_urls[key])}</span>
            )}
          </span>
        </span>
      ))}

      {onReconnect && (
        <button
          className={`strip-action${anyDown ? ' urgent' : ''}`}
          onClick={onReconnect}
        >
          {anyDown ? 'reconnect' : 'change'}
        </button>
      )}

      <span className="strip-env">{health.storage} storage</span>
    </div>
  )
}
