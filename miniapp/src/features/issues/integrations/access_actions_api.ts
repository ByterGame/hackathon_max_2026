import { requestJson } from "../../../shared/base_http_client";

export type AccessRequestKind = "company_registration" | "house_addition" | "resident";
export type AccessRequestStatus = "open" | "reviewing" | "needs_info" | "closed" | "cancelled";

export interface AccessDiscussionMessage {
  id: string;
  author_user_id: string;
  text: string;
  created_at: string;
}

export interface AccessRequestDetail {
  id: string;
  kind: AccessRequestKind;
  status: AccessRequestStatus;
  outcome: string | null;
  decision_note: string | null;
  cancel_requested_by: string | null;
  applicant_user_id?: string | null;
  discussion: AccessDiscussionMessage[];
  created_at: string;
  updated_at: string;
  house_id?: string;
  address_display?: string;
  submitted_full_name?: string;
  submitted_entrance_number?: number;
  submitted_apartment_number?: number;
  entered_address?: string;
  proposed_company_name?: string | null;
  free_text?: string | null;
}

function post<T>(path: string, body: Record<string, unknown>): Promise<T> {
  return requestJson<T>(path, { method: "POST", body: JSON.stringify(body) });
}

export const accessRequestStatusLabels: Record<AccessRequestStatus, string> = {
  open: "Открыта",
  reviewing: "На рассмотрении",
  needs_info: "Нужны уточнения",
  closed: "Закрыта",
  cancelled: "Отменена",
};

export function getAccessRequest(kind: AccessRequestKind, id: string): Promise<AccessRequestDetail> {
  const query = new URLSearchParams({ request_kind: kind, request_id: id });
  return requestJson<{ data: AccessRequestDetail }>(`/access/get_request?${query}`).then((response) => response.data);
}

export function listCompanyRegistrationRequests(): Promise<AccessRequestDetail[]> {
  return requestJson<{ items: AccessRequestDetail[] }>("/access/list_requests?request_kind=company_registration")
    .then((response) => response.items);
}

export function addAccessDiscussionMessage(kind: AccessRequestKind, id: string, text: string): Promise<void> {
  return post("/access/add_discussion_message", { request_kind: kind, request_id: id, text: text.trim() });
}

export function changeAccessRequestStatus(kind: AccessRequestKind, id: string, status: "open" | "reviewing" | "needs_info"): Promise<void> {
  return post("/access/change_request_status", { request_kind: kind, request_id: id, status });
}

export function requestAccessCancellation(kind: AccessRequestKind, id: string): Promise<void> {
  return post("/access/request_cancellation", { request_kind: kind, request_id: id });
}

export function resolveAccessCancellation(kind: AccessRequestKind, id: string, accept: boolean): Promise<void> {
  return post("/access/resolve_cancellation", { request_kind: kind, request_id: id, accept });
}

export function updateResidentAccessRequest(id: string, fullName: string, entrance: number, apartment: number): Promise<void> {
  return post("/access/update_resident_request", {
    request_id: id,
    full_name: fullName.trim(),
    entrance_number: entrance,
    apartment_number: apartment,
  });
}

export function changeResidentGrant(id: string, action: "extend" | "revoke", validTo?: string, reason?: string): Promise<void> {
  return post("/access/change_resident_grant", {
    grant_id: id,
    action,
    valid_to: validTo ?? null,
    reason: reason?.trim() || null,
  });
}

export function revokeStaff(assignmentId: string): Promise<{ id: string; status: string; related_id?: string }> {
  return post("/access/revoke_staff", { assignment_id: assignmentId });
}
