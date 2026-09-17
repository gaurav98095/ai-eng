import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { createEventsTicket, createSession, listChat, sendChat } from "../api";
import type { ChatMessage } from "../types";
import { UploadPanel } from "./UploadPanel";

type Props = { baseUrl: string };

export function SessionWorkspace({ baseUrl }: Props) {
  const [sessionId, setSessionId] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [error, setError] = useState("");

  const startSession = async () => {
    try {
      const session = await createSession(baseUrl);
      setSessionId(session.session_id);
      setMessages([]);
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not create session.");
    }
  };

  useEffect(() => {
    if (!sessionId) return;
    const refresh = async () => {
      try { setMessages(await listChat(baseUrl, sessionId)); } catch { /* keep the last view */ }
    };
    void refresh();
    let source: EventSource | undefined;
    void createEventsTicket(baseUrl, sessionId).then(({ ticket }) => {
      source = new EventSource(`${baseUrl.replace(/\/$/, "")}/sessions/${sessionId}/events?ticket=${encodeURIComponent(ticket)}`);
      source.onmessage = () => void refresh();
    }).catch(() => undefined);
    const timer = window.setInterval(() => void refresh(), 5000);
    return () => { window.clearInterval(timer); source?.close(); };
  }, [baseUrl, sessionId]);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const content = draft.trim();
    if (!sessionId || !content) return;
    try {
      await sendChat(baseUrl, sessionId, content);
      setDraft("");
      setMessages(await listChat(baseUrl, sessionId));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not send message.");
    }
  };

  return (
    <section className="workspace" aria-label="Chat workspace">
      <div className="workspace-head">
        <div><p className="eyebrow">ACTIVE WORKSPACE</p><h3>{sessionId ? "Document conversation" : "Start a workspace"}</h3>{sessionId && <p className="session-id">{sessionId}</p>}</div>
        <button className="secondary" type="button" onClick={() => void startSession()}><span>＋</span> New session</button>
      </div>
      <div className="messages">
        {messages.length === 0 && <div className="empty-state"><span className="empty-icon">✦</span><p>{sessionId ? "Your conversation starts here." : "Create a session to begin."}</p><small>{sessionId ? "Upload a document, then ask anything about it." : "A private space for your documents and questions."}</small></div>}
        {messages.map((message) => (
          <article className={`message ${message.role}`} key={message.id}>
            <span className="message-label">{message.role === "user" ? "You" : "✦ EdgentRAG"}</span>
            <p>{message.content || (message.status === "pending" ? "Waiting for the worker…" : "")}</p>
          </article>
        ))}
      </div>
      <form className="composer" onSubmit={submit}>
        <input value={draft} onChange={(event) => setDraft(event.target.value)} placeholder={sessionId ? "Ask about your documents…" : "Create a session to start chatting"} disabled={!sessionId} />
        <button className="primary send" type="submit" disabled={!sessionId || !draft.trim()}>Send <span>↑</span></button>
      </form>
      {error && <p className="error" role="alert">{error}</p>}
      {sessionId && <UploadPanel baseUrl={baseUrl} sessionId={sessionId} />}
    </section>
  );
}
