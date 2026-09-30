import { useEffect, useState } from "react";

import { Icon } from "../../shared/common_ui/Icon";
import { downloadFile, fetchPrivateFileBlob, type PrivateFile } from "./integrations/client_api";
import "./attachment-preview.css";

type FileKind = "image" | "video" | "pdf" | "file";

function fileKind(mimeType: string, name: string): FileKind {
  if (mimeType.startsWith("image/")) return "image";
  if (mimeType.startsWith("video/") || /\.(mp4|mov)$/i.test(name)) return "video";
  if (mimeType === "application/pdf" || /\.pdf$/i.test(name)) return "pdf";
  return "file";
}

function fileLabel(kind: FileKind): string {
  return { image: "Изображение", video: "Видео", pdf: "Документ PDF", file: "Файл" }[kind];
}

function fileSize(bytes: number): string {
  return bytes < 1024 * 1024 ? `${Math.max(1, Math.round(bytes / 1024))} КиБ` : `${(bytes / (1024 * 1024)).toFixed(1)} МиБ`;
}

function FilePlaceholder({ kind, saved }: { kind: FileKind; saved: boolean }) {
  const hint = kind === "image" ? (saved ? "Можно скачать оригинал" : "Предпросмотр изображения") : (saved ? "Скачайте, чтобы открыть" : "Предпросмотр недоступен");
  return <div className={`attachment-preview__placeholder attachment-preview__placeholder--${kind}`}>
    <span className="attachment-preview__file-icon"><Icon name={kind} size={30} /></span>
    <strong>{fileLabel(kind)}</strong>
    <small>{hint}</small>
  </div>;
}

export function PrivateAttachmentPreview({ file }: { file: PrivateFile }) {
  const [url, setUrl] = useState<string | null>(null);
  const [error, setError] = useState("");
  const kind = fileKind(file.mime_type, file.original_name);

  useEffect(() => {
    setUrl(null);
    setError("");
    if (kind !== "image") return;
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
      .catch((reason) => { if (active) setError(reason instanceof Error ? reason.message : "Не удалось открыть изображение"); });
    return () => {
      active = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [file.id, kind]);

  async function save() {
    setError("");
    try { await downloadFile(file); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Не удалось скачать вложение"); }
  }

  return <div className="attachment-preview">
    {url ? <img className="attachment-preview__media" src={url} alt={file.original_name} loading="lazy" onError={() => { setUrl(null); setError("Не удалось показать изображение. Можно скачать файл."); }} /> : <FilePlaceholder kind={kind} saved />}
    <div className="attachment-preview__footer"><span className="attachment-preview__filename" title={file.original_name}>{file.original_name}<small>{fileSize(file.size_bytes)}</small></span><button type="button" className="button button--soft" onClick={() => void save()}><Icon name="download" size={16} /> Скачать</button></div>
    {error && <p className="form-error" role="alert">{error}</p>}
  </div>;
}

export function SelectedAttachmentPreview({ file, onRemove }: { file: File; onRemove?: () => void }) {
  const [url, setUrl] = useState<string | null>(null);
  const kind = fileKind(file.type, file.name);

  useEffect(() => {
    setUrl(null);
    if (kind !== "image") return;
    const objectUrl = URL.createObjectURL(file);
    setUrl(objectUrl);
    return () => URL.revokeObjectURL(objectUrl);
  }, [file, kind]);

  return <div className="attachment-preview">
    {url ? <img className="attachment-preview__media" src={url} alt={file.name} onError={() => setUrl(null)} /> : <FilePlaceholder kind={kind} saved={false} />}
    <div className="attachment-preview__footer"><span className="attachment-preview__filename" title={file.name}>{file.name}<small>{fileSize(file.size)}</small></span>{onRemove && <button type="button" className="button button--soft" onClick={onRemove}>Убрать</button>}</div>
  </div>;
}
