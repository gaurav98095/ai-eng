import { useEffect, useState } from "react";
import type { DragEvent } from "react";
import { completeUpload, createUploadTargets, getSession, putUpload } from "../api";
import type { SessionFile } from "../types";

type Props = { baseUrl: string; sessionId: string };

function contentTypeFor(file: File): string {
  const extension = file.name.toLowerCase().split(".").pop();
  const types: Record<string, string> = {
    aac: "audio/aac", doc: "application/msword",
    docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    m4a: "audio/mp4", mkv: "video/x-matroska", mov: "video/quicktime",
    mp3: "audio/mpeg", mp4: "video/mp4", md: "text/markdown", ogg: "audio/ogg",
    pdf: "application/pdf", txt: "text/plain", wav: "audio/wav", webm: "video/webm",
  };
  return types[extension || ""] || file.type || "application/octet-stream";
}

export function UploadPanel({ baseUrl, sessionId }: Props) {
  const [files, setFiles] = useState<SessionFile[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [uploadingName, setUploadingName] = useState("");

  const refresh = async () => {
    try { setFiles((await getSession(baseUrl, sessionId)).files); } catch { /* retain last state */ }
  };

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), 3000);
    return () => window.clearInterval(timer);
  }, [baseUrl, sessionId]);

  const upload = async (selected: FileList | null) => {
    if (!selected?.length) return;
    setBusy(true);
    setError("");
    try {
      const localFiles = Array.from(selected);
      const { targets } = await createUploadTargets(baseUrl, sessionId, localFiles.map((file) => ({
        filename: file.name,
        content_type: contentTypeFor(file),
        size_bytes: file.size,
      })));
      for (const target of targets) {
        const file = localFiles.find((item) => item.name === target.filename);
        if (!file) continue;
        setUploadingName(file.name);
        await putUpload(target, file);
        await completeUpload(baseUrl, sessionId, target.file_id);
      }
      await refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Upload failed.");
    } finally { setUploadingName(""); setBusy(false); }
  };

  const acceptDrop = (event: DragEvent<HTMLDivElement>) => { event.preventDefault(); if (!busy) void upload(event.dataTransfer.files); };

  return (
    <section className="upload-panel" aria-label="Source uploads">
      <div className="workspace-head"><div><p className="eyebrow">SOURCE LIBRARY</p><h3>Your documents and recordings</h3><p className="muted">Text, PDF, Word, audio, and video</p></div>
        <label className="button"><input type="file" multiple accept=".md,.txt,.pdf,.doc,.docx,.aac,.m4a,.mp3,.ogg,.wav,.mp4,.mov,.mkv,.webm" disabled={busy} onChange={(event) => void upload(event.target.files)} /><span>＋</span>{busy ? "Uploading…" : "Add sources"}</label>
      </div>
      {files.length === 0 ? <div className="drop-hint" onDragOver={(event) => event.preventDefault()} onDrop={acceptDrop}><span>⇧</span><strong>Drop files here</strong><small>or browse · Markdown, text, PDF, Word, audio, and video</small></div> : <ul className="file-list">{files.map((file) => <li key={file.file_id}><span className="file-name"><span className="file-icon">{file.kind === "audio" ? "◉" : "↗"}</span>{file.filename}</span><span className={`status ${file.status}`}>{uploadingName === file.filename ? "uploading…" : file.status}</span></li>)}</ul>}
      {error && <p className="error" role="alert">{error}</p>}
    </section>
  );
}
