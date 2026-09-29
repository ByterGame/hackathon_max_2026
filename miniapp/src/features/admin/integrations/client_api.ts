import { requestJson, requestResponse } from "../../../shared/base_http_client";

export type AdminEntity =
  | "users"
  | "companies"
  | "houses"
  | "apartments"
  | "issues"
  | "access_requests"
  | "staff"
  | "resident_grants"
  | "offers"
  | "support_invites"
  | "files"
  | "audit";

export interface AdminListItem {
  id: string;
  title: string;
  subtitle?: string | null;
  status?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  data?: Record<string, unknown>;
}

export interface AdminPage {
  items: AdminListItem[];
  total: number;
  limit: number;
  offset: number;
}

export interface AdminOverview {
  counts: Record<string, number>;
}

export interface AdminIssueCategory {
  id: string;
  code: string;
  name: string;
}

export interface SupportInvitation {
  id: string;
  phone_number: string;
  created_at: string;
}

export interface AdminSystemField {
  name: string;
  type: "text" | "int" | "bool" | "uuid" | "datetime" | "json";
  nullable: boolean;
  editable: boolean;
  foreign_key?: string | null;
  clear_only?: boolean;
}

export interface AdminSystemEntity {
  key: string;
  label: string;
  key_fields: string[];
  fields: AdminSystemField[];
  delete_mode: "soft" | "hard";
  delete_modes: ("soft" | "hard")[];
}

export interface AdminSystemRow {
  id: string;
  etag: string;
  data: Record<string, unknown>;
}

export interface AdminSystemPage {
  items: AdminSystemRow[];
  total: number;
  limit: number;
  offset: number;
}

export interface AdminSystemDetail {
  item: AdminSystemRow;
  dependencies: { entity: string; field: string; count: number }[];
  delete_mode: "soft" | "hard";
  delete_modes: ("soft" | "hard")[];
}

export interface AdminSystemOperation {
  id: string;
  entity_key: string;
  row_key: string;
  operation: string;
  actor_user_id: string;
  reason: string;
  expected_etag: string;
  before_data: Record<string, unknown>;
  after_data: Record<string, unknown> | null;
  created_at: string;
}

export interface AdminSystemOperationsPage {
  items: AdminSystemOperation[];
  total: number;
  limit: number;
  offset: number;
}

export function getAdminSystemSchema(): Promise<AdminSystemEntity[]> {
  return requestJson<{ entities: AdminSystemEntity[] }>("/admin/system/schema").then(({ entities }) => entities);
}

export function listAdminSystemRows(entity: string, query: string, offset: number, limit = 20): Promise<AdminSystemPage> {
  const params = new URLSearchParams({ entity, q: query, offset: String(offset), limit: String(limit) });
  return requestJson<AdminSystemPage>(`/admin/system/list?${params}`);
}

export function getAdminSystemRow(entity: string, id: string): Promise<AdminSystemDetail> {
  const params = new URLSearchParams({ entity, id });
  return requestJson<AdminSystemDetail>(`/admin/system/get?${params}`);
}

export function patchAdminSystemRow(entity: string, id: string, etag: string, reason: string, changes: Record<string, unknown>): Promise<{ item: AdminSystemRow; operation_id: string }> {
  return requestJson("/admin/system/patch", { method: "POST", body: JSON.stringify({ entity, id, expected_etag: etag, reason, changes }) });
}

export function deleteAdminSystemRow(entity: string, id: string, etag: string, reason: string, mode: "soft" | "hard"): Promise<{ item: AdminSystemRow | null; operation_id: string; mode: "soft" | "hard" }> {
  return requestJson("/admin/system/delete", { method: "POST", body: JSON.stringify({ entity, id, expected_etag: etag, reason, mode }) });
}

export function listAdminSystemOperations(query: string, offset: number, limit = 20): Promise<AdminSystemOperationsPage> {
  const params = new URLSearchParams({ q: query, offset: String(offset), limit: String(limit) });
  return requestJson<AdminSystemOperationsPage>(`/admin/system/operations?${params}`);
}

export function getAdminOverview(): Promise<AdminOverview> {
  return requestJson<AdminOverview>("/admin/overview");
}

export function getAdminIssueCategories(): Promise<AdminIssueCategory[]> {
  return requestJson<{ categories: AdminIssueCategory[] }>("/issues/list_categories").then(({ categories }) => categories);
}

export function listAdminItems(entity: AdminEntity, query: string, offset: number, limit = 20, kind?: string): Promise<AdminPage> {
  const params = new URLSearchParams({ entity, q: query, offset: String(offset), limit: String(limit) });
  if (entity === "access_requests" && kind) params.set("kind", kind);
  return requestJson<AdminPage>(`/admin/list?${params}`);
}

export function getAdminItem(entity: AdminEntity, id: string): Promise<Record<string, unknown>> {
  const params = new URLSearchParams({ entity, id });
  return requestJson<{ item: Record<string, unknown> }>(`/admin/get?${params}`).then(({ item }) => item);
}

export function runAdminAction(action: string, payload: Record<string, unknown>): Promise<unknown> {
  return requestJson("/admin/action", { method: "POST", body: JSON.stringify({ action, payload }) });
}

export function getMySupportInvitations(): Promise<SupportInvitation[]> {
  return requestJson<{ items: SupportInvitation[] }>("/admin/support-invites/mine").then(({ items }) => items);
}

export function acceptSupportInvitation(invitationId: string): Promise<void> {
  return requestJson("/admin/support-invites/accept", { method: "POST", body: JSON.stringify({ invitation_id: invitationId }) });
}

export async function downloadAdminFile(id: string, filename: string): Promise<void> {
  const params = new URLSearchParams({ file_id: id });
  const response = await requestResponse(`/files/download?${params}`);
  const url = URL.createObjectURL(await response.blob());
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 30_000);
}
