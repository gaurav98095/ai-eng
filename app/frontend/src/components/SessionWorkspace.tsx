import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { createEventsTicket, createSession, listChat, sendChat } from "../api";
import type { ChatMessage } from "../types";
import { UploadPanel } from "./UploadPanel";

type Props = { baseUrl: string };

function loadRecentSessions(): string[] {
  try {
    const value: unknown = JSON.parse(
      window.localStorage.getItem("edgentrag.sessions") || "[]",
    );
    return Array.isArray(value)
      ? value.filter((item): item is string => typeof item === "string")
      : [];
  } catch {
    return [];
  }
}

export function SessionWorkspace({ baseUrl }: Props) {
  const [sessionId, setSessionId] = useState("");
  const [recentSessions, setRecentSessions] =
    useState<string[]>(loadRecentSessions);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [error, setError] = useState("");

  const startSession = async () => {
    try {
      const session = await createSession(baseUrl);
      setSessionId(session.session_id);
      setRecentSessions((current) => {
        const next = [
          session.session_id,
          ...current.filter((id) => id !== session.session_id),
        ].slice(0, 8);
        window.localStorage.setItem(
          "edgentrag.sessions",
          JSON.stringify(next),
        );
        return next;
      });
      setMessages([]);
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not create session.");
    }
  };

  const selectSession = (id: string) => {
    setSessionId(id);
    setError("");
  };

  useEffect(() => {
    if (!sessionId) return;
    let cancelled = false;
    const refresh = async () => {
      try {
        const next = await listChat(baseUrl, sessionId);
        if (!cancelled) setMessages(next);
      } catch {
        // Keep the last successfully loaded view.
      }
    };
    void refresh();
    let source: EventSource | undefined;
    void createEventsTicket(baseUrl, sessionId)
      .then(({ ticket }) => {
        if (cancelled) return;
        source = new EventSource(
          `${baseUrl.replace(/\/$/, "")}/sessions/${sessionId}/events?ticket=${encodeURIComponent(ticket)}`,
        );
        source.onmessage = () => void refresh();
      })
      .catch(() => undefined);
    const timer = window.setInterval(() => void refresh(), 5000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
      source?.close();
    };
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
        <div className="workspace-actions"><select aria-label="Recent sessions" value={sessionId} onChange={(event) => selectSession(event.target.value)}><option value="">Recent sessions</option>{recentSessions.map((id) => <option key={id} value={id}>{id.slice(0, 8)}…</option>)}</select><button className="secondary" type="button" onClick={() => void startSession()}><span>＋</span> New session</button></div>
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
