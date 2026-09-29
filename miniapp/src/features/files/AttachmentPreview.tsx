import { useEffect, useState } from "react";

import { downloadFile, fetchPrivateFileBlob, type PrivateFile } from "./integrations/client_api";
import "./attachment-preview.css";

function isPreviewable(mimeType: string): boolean {
  return mimeType.startsWith("image/") || mimeType.startsWith("video/") || mimeType === "application/pdf";
}

function PreviewMedia({ url, mimeType, name }: { url: string; mimeType: string; name: string }) {
  if (mimeType.startsWith("image/")) return <img className="attachment-preview__media" src={url} alt={name} loading="lazy" />;
  if (mimeType.startsWith("video/")) return <video className="attachment-preview__media" src={url} controls preload="metadata" aria-label={name} />;
  if (mimeType === "application/pdf") return <iframe className="attachment-preview__pdf" src={url} title={name} sandbox="allow-same-origin" loading="lazy" />;
  return null;
}

export function PrivateAttachmentPreview({ file }: { file: PrivateFile }) {
  const [url, setUrl] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(isPreviewable(file.mime_type));

  useEffect(() => {
    setUrl(null);
    setError("");
    setLoading(isPreviewable(file.mime_type));
    if (!isPreviewable(file.mime_type)) return;
    let active = true;
    let objectUrl: string | null = null;
    void fetchPrivateFileBlob(file.id)
      .then((blob) => {
        const created = URL.createObjectURL(blob);
        if (active) {
          objectUrl = created;
          setUrl(created);
        } else {
          URL.revokeObjectURL(created);
        }
      })
      .catch((reason) => { if (active) setError(reason instanceof Error ? reason.message : "Не удалось открыть вложение"); })
      .finally(() => { if (active) setLoading(false); });
    return () => {
      active = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [file.id, file.mime_type]);

  async function save() {
    setError("");
    try { await downloadFile(file); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Не удалось скачать вложение"); }
  }

  return <div className="attachment-preview">
    {url && <PreviewMedia url={url} mimeType={file.mime_type} name={file.original_name} />}
    {loading && <span className="attachment-preview__hint">Загружаем вложение…</span>}
    <div className="attachment-preview__footer"><span title={file.original_name}>{file.original_name}</span><button type="button" className="button button--soft" onClick={() => void save()}>Скачать</button></div>
    {error && <p className="form-error" role="alert">{error}</p>}
  </div>;
}

export function SelectedAttachmentPreview({ file, onRemove }: { file: File; onRemove?: () => void }) {
  const [url, setUrl] = useState<string | null>(null);

  useEffect(() => {
    setUrl(null);
    if (!isPreviewable(file.type)) return;
    const objectUrl = URL.createObjectURL(file);
    setUrl(objectUrl);
    return () => URL.revokeObjectURL(objectUrl);
  }, [file]);

  return <div className="attachment-preview">
    {url && <PreviewMedia url={url} mimeType={file.type} name={file.name} />}
    <div className="attachment-preview__footer"><span title={file.name}>{file.name}</span>{onRemove && <button type="button" className="button button--soft" onClick={onRemove}>Убрать</button>}</div>
  </div>;
}
