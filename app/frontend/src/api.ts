import type { ChatMessage, Session, SessionDetail } from "./types";

export type HealthResponse = { status: string };

export async function checkHealth(baseUrl: string): Promise<HealthResponse> {
  const response = await fetch(`${baseUrl.replace(/\/$/, "")}/health`);
  if (!response.ok) {
    let detail = "";
    try {
      const payload = (await response.json()) as { detail?: string };
      detail = payload.detail ? `: ${payload.detail}` : "";
    } catch {
      // Keep the status-only message for non-JSON error responses.
    }
    throw new Error(`Backend returned HTTP ${response.status}${detail}`);
  }
  return (await response.json()) as HealthResponse;
}

async function request<T>(baseUrl: string, path: string, init?: RequestInit): Promise<T> {
  const token = window.localStorage.getItem("edgentrag.id_token");
  const response = await fetch(`${baseUrl.replace(/\/$/, "")}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}), ...init?.headers },
  });
  if (!response.ok) throw new Error(`Backend returned HTTP ${response.status}`);
  return (await response.json()) as T;
}

export const createSession = (baseUrl: string) =>
  request<Session>(baseUrl, "/sessions", { method: "POST" });

export const getSession = (baseUrl: string, sessionId: string) =>
  request<SessionDetail>(baseUrl, `/sessions/${sessionId}`);

export type UploadTarget = {
  file_id: string;
  filename: string;
  content_type: string;
  upload_url: string;
  expires_in: number;
};

export const createUploadTargets = (
  baseUrl: string,
  sessionId: string,
  files: Array<{ filename: string; content_type: string; size_bytes: number }>,
) =>
  request<{ session_id: string; targets: UploadTarget[] }>(
    baseUrl,
    `/sessions/${sessionId}/uploads`,
    { method: "POST", body: JSON.stringify({ files }) },
  );

export async function putUpload(target: UploadTarget, file: File): Promise<void> {
  // Containers reach Floci through Docker's host gateway, but the browser
  // must use the host-published address instead.
  const browserUploadUrl = target.upload_url.replace(
    "http://host.docker.internal:4566",
    "http://localhost:4566",
  );
  const response = await fetch(browserUploadUrl, {
    method: "PUT",
    headers: { "Content-Type": target.content_type },
    body: file,
  });
  if (!response.ok) throw new Error(`Storage returned HTTP ${response.status}`);
}

export const completeUpload = (baseUrl: string, sessionId: string, fileId: string) =>
  request<{ status: string }>(baseUrl, `/sessions/${sessionId}/uploads/${fileId}/complete`, {
    method: "POST",
  });

export const listChat = (baseUrl: string, sessionId: string) =>
  request<ChatMessage[]>(baseUrl, `/sessions/${sessionId}/chat`);

export const sendChat = (baseUrl: string, sessionId: string, content: string) =>
  request<{ message_id: string }>(baseUrl, `/sessions/${sessionId}/chat`, {
    method: "POST",
    body: JSON.stringify({ content }),
  });

export const createEventsTicket = (baseUrl: string, sessionId: string) =>
  request<{ ticket: string; expires_in: number }>(baseUrl, `/sessions/${sessionId}/events/ticket`, { method: "POST" });
