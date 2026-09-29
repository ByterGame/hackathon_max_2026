import { requestJson, requestResponse } from "../../../shared/base_http_client";

export type FileParent = "issue_report" | "issue_message" | "company_registration" | "house_addition";

export interface PrivateFile {
  id: string;
  draft_id?: string | null;
  parent_kind?: FileParent | null;
  parent_id?: string | null;
  original_name: string;
  mime_type: string;
  size_bytes: number;
  state: string;
  created_at: string;
}

export async function uploadFile(file: File, draftId: string): Promise<PrivateFile> {
  const params = new URLSearchParams({ filename: file.name, draft_id: draftId });
  const response = await requestJson<{ file: PrivateFile }>(`/files/upload?${params}`, {
    method: "POST",
    headers: { "Content-Type": file.type || "application/octet-stream" },
    body: file,
  });
  return response.file;
}

export async function uploadParentFile(file: File, parentKind: FileParent, parentId: string): Promise<PrivateFile> {
  const params = new URLSearchParams({ filename: file.name, parent_kind: parentKind, parent_id: parentId });
  const response = await requestJson<{ file: PrivateFile }>(`/files/upload?${params}`, {
    method: "POST",
    headers: { "Content-Type": file.type || "application/octet-stream" },
    body: file,
  });
  return response.file;
}

export async function attachFile(fileId: string, parentKind: FileParent, parentId: string): Promise<PrivateFile> {
  const response = await requestJson<{ file: PrivateFile }>("/files/attach", {
    method: "POST",
    body: JSON.stringify({ file_id: fileId, parent_kind: parentKind, parent_id: parentId }),
  });
  return response.file;
}

export async function listFiles(parentKind: FileParent, parentId: string): Promise<PrivateFile[]> {
  const params = new URLSearchParams({ parent_kind: parentKind, parent_id: parentId });
  const response = await requestJson<{ files: PrivateFile[] }>(`/files/list?${params}`);
  return response.files;
}

export async function downloadFile(file: PrivateFile): Promise<void> {
  const params = new URLSearchParams({ file_id: file.id });
  const response = await requestResponse(`/files/download?${params}`);
  const url = URL.createObjectURL(await response.blob());
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = file.original_name;
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 30_000);
}
