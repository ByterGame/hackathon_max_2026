import { requestJson } from "../../../shared/base_http_client";

export interface StaffMergeSuggestion {
  similar_card_ids: string[];
  source: "gigachat" | "local";
}

export function suggestStaffMerges(cardId: string): Promise<StaffMergeSuggestion> {
  return requestJson<StaffMergeSuggestion>("/issues/suggest_merge", {
    method: "POST",
    body: JSON.stringify({ card_id: cardId }),
  });
}
