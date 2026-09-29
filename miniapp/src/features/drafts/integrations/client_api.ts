import { requestJson } from "../../../shared/base_http_client";

export type DraftFlow = "issue_card" | "resident_request";

export interface DraftData {
  id: string;
  flow_kind: DraftFlow;
  payload: Record<string, unknown>;
  revision: number;
  created_at: string;
  updated_at: string;
  submitted_at?: string | null;
}

export async function listDrafts(flow: DraftFlow): Promise<DraftData[]> {
  const response = await requestJson<{ items: DraftData[] }>(`/drafts/list?${new URLSearchParams({ flow_kind: flow })}`);
  return response.items;
}

export async function getDraft(id: string): Promise<DraftData> {
  const response = await requestJson<{ draft: DraftData }>(`/drafts/get?${new URLSearchParams({ draft_id: id })}`);
  return response.draft;
}

export async function saveDraft(flow: DraftFlow, payload: Record<string, unknown>, existing?: DraftData | null): Promise<DraftData> {
  const response = await requestJson<{ draft: DraftData }>("/drafts/save", {
    method: "POST",
    body: JSON.stringify({ flow_kind: flow, payload, ...(existing ? { draft_id: existing.id, revision: existing.revision } : {}) }),
  });
  return response.draft;
}

export async function submitDraft(draft: DraftData): Promise<DraftData> {
  const response = await requestJson<{ draft: DraftData }>("/drafts/submit", {
    method: "POST",
    body: JSON.stringify({ draft_id: draft.id, revision: draft.revision }),
  });
  return response.draft;
}
