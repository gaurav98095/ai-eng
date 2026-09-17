import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { useState } from "react";
import { checkHealth } from "./api";
import { SessionWorkspace } from "./components/SessionWorkspace";
import "./styles.css";

function App() {
  const [baseUrl, setBaseUrl] = useState("http://127.0.0.1:8000");
  const [connected, setConnected] = useState(false);
  const [page, setPage] = useState<"overview" | "workspace" | "settings">("overview");
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
      if (result.status === "ok") setPage("overview");
      if (result.status !== "ok") setError("The backend is reachable but unhealthy.");
    } catch (reason) {
      setConnected(false);
      setError(reason instanceof Error ? reason.message : "Could not reach backend.");
    }
  };
  if (!connected) return (
    <main className="shell">
      <header className="header">
        <div className="brand"><span className="brand-mark">E</span><div><p className="eyebrow">DOCUMENT INTELLIGENCE</p><h1>Edgent<span>RAG</span></h1></div></div>
        <span className="status"><i className={connected ? "ok" : ""} /> {connected ? "API connected" : "API not connected"}</span>
      </header>
      <section className="welcome" aria-labelledby="welcome-title">
        <div className="hero-copy"><p className="eyebrow">YOUR KNOWLEDGE, IN CONTEXT</p>
        <h2 id="welcome-title">Ask better questions.<br /><em>Get grounded answers.</em></h2>
        <p className="muted">Connect your workspace, add documents, and chat with an assistant that keeps every answer close to the source.</p></div>
        <div className="feature-row"><span>✦ Semantic search</span><span>◌ Source-aware answers</span><span>↗ Private by default</span></div>
        <form className="connect" onSubmit={(event) => { event.preventDefault(); void connect(); }}>
          <label htmlFor="backend-url">Backend URL</label>
          <div className="connect-row">
            <input id="backend-url" value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} type="url" required />
            <button className="primary" type="submit">Connect backend <span>→</span></button>
          </div>
          <label htmlFor="id-token">Cognito ID token (optional locally)</label>
          <input id="id-token" value={token} onChange={(event) => setToken(event.target.value)} onBlur={saveToken} type="password" placeholder="Paste an ID token for production" />
          {error && <p className="error" role="alert">{error}</p>}
        </form>
      </section>
    </main>
  );

  return (
    <main className="app-shell">
      <aside className="sidebar">
        <div className="brand"><span className="brand-mark">E</span><div><h1>Edgent<span>RAG</span></h1><small>Knowledge workspace</small></div></div>
        <nav aria-label="Primary navigation">
          <button className={page === "overview" ? "nav-item active" : "nav-item"} onClick={() => setPage("overview")}><span>⌂</span> Overview</button>
          <button className={page === "workspace" ? "nav-item active" : "nav-item"} onClick={() => setPage("workspace")}><span>✦</span> Workspace</button>
          <button className={page === "settings" ? "nav-item active" : "nav-item"} onClick={() => setPage("settings")}><span>⚙</span> Settings</button>
        </nav>
        <div className="sidebar-footer"><span className="status"><i className="ok" /> API connected</span><button className="disconnect" onClick={() => setConnected(false)}>Disconnect</button></div>
      </aside>
      <section className="content-area">
        <header className="content-header"><div><p className="eyebrow">{page === "overview" ? "OVERVIEW" : page === "workspace" ? "WORKSPACE" : "SETTINGS"}</p><h2 className="page-title">{page === "overview" ? "Good to have you back." : page === "workspace" ? "Your document conversation" : "Workspace settings"}</h2></div><span className="header-dot">● Local development</span></header>
        {page === "overview" && <section className="dashboard"><div className="dashboard-card dashboard-hero"><p className="eyebrow">GET STARTED</p><h3>Turn your documents<br /><em>into answers.</em></h3><p className="muted">Create a private workspace, upload your source material, and ask questions with context you can trust.</p><button className="primary" onClick={() => setPage("workspace")}>Open workspace <span>→</span></button></div><div className="dashboard-card quick-card"><span className="card-icon">✦</span><h3>Start a new session</h3><p className="muted">A clean conversation for a new set of documents.</p><button className="secondary" onClick={() => setPage("workspace")}>Create session <span>＋</span></button></div><div className="dashboard-card principles"><p className="eyebrow">BUILT FOR FOCUS</p><div><strong>01</strong><span>Bring your sources</span></div><div><strong>02</strong><span>Ask naturally</span></div><div><strong>03</strong><span>Stay grounded</span></div></div></section>}
        {page === "workspace" && <SessionWorkspace baseUrl={baseUrl} />}
        {page === "settings" && <section className="settings-card"><p className="eyebrow">CONNECTION</p><h3>Backend connection</h3><p className="muted">These settings are kept in this browser for local development.</p><label htmlFor="settings-url">Backend URL</label><input id="settings-url" value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} type="url" /><label htmlFor="settings-token">Cognito ID token</label><input id="settings-token" value={token} onChange={(event) => setToken(event.target.value)} onBlur={saveToken} type="password" placeholder="Optional locally" /><button className="primary" onClick={() => void connect()}>Test connection <span>→</span></button></section>}
      </section>
    </main>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode><App /></StrictMode>,
);
