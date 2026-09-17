export type Session = { session_id: string; status: string };
export type SessionDetail = Session & { created_at: string; files: SessionFile[] };
export type SessionFile = {
  file_id: string;
  filename: string;
  content_type: string;
  kind: "document" | "audio";
  size_bytes: number;
  status: string;
};
export type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string | null;
  status: "pending" | "answering" | "done" | "failed";
  sources: unknown[] | null;
};
