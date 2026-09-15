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
        <div><p className="eyebrow">SESSION</p><p className="session-id">{sessionId || "No session selected"}</p></div>
        <button type="button" onClick={() => void startSession()}>New session</button>
      </div>
      <div className="messages">
        {messages.length === 0 && <p className="muted">Create a session to begin.</p>}
        {messages.map((message) => (
          <article className={`message ${message.role}`} key={message.id}>
            <span>{message.role === "user" ? "You" : "EdgentRAG"}</span>
            <p>{message.content || (message.status === "pending" ? "Waiting for the worker…" : "")}</p>
          </article>
        ))}
      </div>
      <form className="composer" onSubmit={submit}>
        <input value={draft} onChange={(event) => setDraft(event.target.value)} placeholder="Ask about your documents…" disabled={!sessionId} />
        <button type="submit" disabled={!sessionId || !draft.trim()}>Send</button>
      </form>
      {error && <p className="error" role="alert">{error}</p>}
      {sessionId && <UploadPanel baseUrl={baseUrl} sessionId={sessionId} />}
    </section>
  );
}
