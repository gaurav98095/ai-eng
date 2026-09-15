import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { useState } from "react";
import { checkHealth } from "./api";
import { SessionWorkspace } from "./components/SessionWorkspace";
import "./styles.css";

function App() {
  const [baseUrl, setBaseUrl] = useState("http://127.0.0.1:8000");
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState("");
  const [token, setToken] = useState(() => window.localStorage.getItem("edgentrag.id_token") ?? "");
  const saveToken = () => {
    if (token.trim()) window.localStorage.setItem("edgentrag.id_token", token.trim());
    else window.localStorage.removeItem("edgentrag.id_token");
  };
  const connect = async () => {
    setError("");
    try {
      const result = await checkHealth(baseUrl);
      setConnected(result.status === "ok");
      if (result.status !== "ok") setError("The backend is reachable but unhealthy.");
    } catch (reason) {
      setConnected(false);
      setError(reason instanceof Error ? reason.message : "Could not reach backend.");
    }
  };
  return (
    <main className="shell">
      <header className="header">
        <div>
          <p className="eyebrow">DOCUMENT INTELLIGENCE</p>
          <h1>EdgentRAG</h1>
        </div>
        <span className="status"><i className={connected ? "ok" : ""} /> {connected ? "API connected" : "API not connected"}</span>
      </header>
      <section className="welcome" aria-labelledby="welcome-title">
        <p className="eyebrow">WORKSPACE</p>
        <h2 id="welcome-title">Ask questions grounded in your documents.</h2>
        <p className="muted">Connect the backend to create a session, upload files, and start a cited conversation.</p>
        <form className="connect" onSubmit={(event) => { event.preventDefault(); void connect(); }}>
          <label htmlFor="backend-url">Backend URL</label>
          <div className="connect-row">
            <input id="backend-url" value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} type="url" required />
            <button type="submit">Connect backend</button>
          </div>
          <label htmlFor="id-token">Cognito ID token (optional locally)</label>
          <input id="id-token" value={token} onChange={(event) => setToken(event.target.value)} onBlur={saveToken} type="password" placeholder="Paste an ID token for production" />
          {error && <p className="error" role="alert">{error}</p>}
        </form>
      </section>
      {connected && <SessionWorkspace baseUrl={baseUrl} />}
    </main>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode><App /></StrictMode>,
);
