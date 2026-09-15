import { useEffect, useState } from "react";
import { completeUpload, createUploadTargets, getSession, putUpload } from "../api";
import type { SessionFile } from "../types";

type Props = { baseUrl: string; sessionId: string };

function contentTypeFor(file: File): string {
  return file.name.toLowerCase().endsWith(".md") ? "text/markdown" : "text/plain";
}

export function UploadPanel({ baseUrl, sessionId }: Props) {
  const [files, setFiles] = useState<SessionFile[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

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
        await putUpload(target, file);
        await completeUpload(baseUrl, sessionId, target.file_id);
      }
      await refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Upload failed.");
    } finally { setBusy(false); }
  };

  return (
    <section className="upload-panel" aria-label="Document uploads">
      <div className="workspace-head"><div><p className="eyebrow">DOCUMENTS</p><p className="muted">Upload Markdown or plain text files</p></div>
        <label className="button"><input type="file" multiple accept=".md,.txt,text/markdown,text/plain" disabled={busy} onChange={(event) => void upload(event.target.files)} />{busy ? "Uploading…" : "Choose files"}</label>
      </div>
      {files.length === 0 ? <p className="muted">No files uploaded yet.</p> : <ul className="file-list">{files.map((file) => <li key={file.file_id}><span>{file.filename}</span><span className={`status ${file.status}`}>{file.status}</span></li>)}</ul>}
      {error && <p className="error" role="alert">{error}</p>}
    </section>
  );
}
