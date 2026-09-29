import { getCurrentUser, type CurrentUser } from "../../auth/integrations/client_api";
import { requestJson } from "../../../shared/base_http_client";
import {
  issueStatusLabels,
  type CompanyRegistrationRequest,
  type House,
  type HouseAdditionRequest,
  type Issue,
  type IssueCategory,
  type IssueEvent,
  type IssueMessage,
  type IssueStatus,
  type ResidentGrant,
  type ResidentOffer,
  type ResidentRequest,
  type StaffAssignment,
} from "../types";
import type { AppSnapshot, IssuesClient, IssueSuggestion, NotificationSubject } from "./client_api";

interface WireCard {
  id: string;
  house_id: string;
  author_user_id: string;
  category_id: string;
  title: string;
  version: number;
  status: IssueStatus;
  close_result: "solved" | "invalid" | null;
  current_note: string | null;
  scope_all_house: boolean;
  support_count: number;
  supported_by_me: boolean | null;
  created_at: string;
  updated_at: string;
}

interface WireDetail {
  card: WireCard;
  targets: { id: string; entrance_number: number | null; apartment_id: string | null; apartment_entrance_number: number | null; apartment_number: number | null }[];
  reports: { id: string; author_user_id: string; raw_description: string; created_at: string }[];
  messages: { id: string; author_user_id: string; kind: "resident_comment" | "official_uk"; body: string; created_at: string }[];
  history: { id: string; action: string; actor_user_id: string; status: IssueStatus | null; note: string | null; created_at: string }[];
}

interface WireRequest {
  id: string;
  kind: string;
  status: ResidentRequest["status"];
  outcome?: "granted" | "denied" | null;
  decision_note?: string | null;
  decided_at?: string | null;
  created_at: string;
  house_id?: string;
  address_display?: string | null;
  submitted_full_name?: string;
  submitted_entrance_number?: number;
  submitted_apartment_number?: number;
  phone_number?: string;
  proposed_company_name?: string | null;
  free_text?: string | null;
  registration_request_id?: string | null;
  company_id?: string | null;
  entered_address?: string;
}

interface WireGrant {
  id: string;
  house_id: string;
  address_display: string;
  entrance_number: number;
  apartment_number: number;
  valid_to: string | null;
  status: "active" | "expired" | "revoked";
}

interface WireOffer {
  id: string;
  house_id: string;
  address_display: string;
  entrance_number: number;
  apartment_number: number;
  status: ResidentOffer["status"];
  created_at: string;
}

interface WireCompanyOffer extends WireOffer {
  phone_number: string;
}

interface WireResident {
  grant_id: string;
  full_name: string | null;
  phone_number: string | null;
  house_id: string;
  address_display: string;
  entrance_number: number;
  apartment_number: number;
  status: "active" | "expired" | "revoked";
  valid_to: string | null;
  decided_by: string;
  decided_at: string | null;
}

interface WireStaff {
  id: string;
  phone_number: string;
  user_id: string | null;
  can_manage_staff: boolean;
  can_manage_residents: boolean;
  can_manage_issues: boolean;
  created_at: string;
  revoked_at: string | null;
}

let categoriesCache: IssueCategory[] = [];
let housesCache: House[] = [];
let currentUserCache: CurrentUser | null = null;
let lastSnapshot: AppSnapshot | null = null;

function query(path: string, params: Record<string, string>): string {
  return `${path}?${new URLSearchParams(params)}`;
}

function post<T>(path: string, body: Record<string, unknown>, idempotencyKey?: string): Promise<T> {
  return requestJson<T>(path, { method: "POST", headers: idempotencyKey ? { "Idempotency-Key": idempotencyKey } : undefined, body: JSON.stringify(body) });
}

function rememberHouse(house: House, map: Map<string, House>) {
  map.set(house.id, house);
}

function issueEventLabel(event: WireDetail["history"][number]): string {
  if (event.action.includes("creat")) return "Проблема создана";
  if (event.action.includes("reopen")) return "Проблема переоткрыта";
  if (event.action.includes("merg")) return "Проблемы объединены";
  if (event.status === "closed") return "Проблема закрыта";
  if (event.status) return issueStatusLabels[event.status] ?? "Статус обновлён";
  if (event.action.includes("support")) return "Проблему поддержали";
  if (event.action.includes("comment")) return "Добавлен комментарий";
  return "Карточка обновлена";
}

function toIssue(detail: WireDetail, user: CurrentUser): Issue {
  const card = detail.card;
  const company = housesCache.find((item) => item.id === card.house_id)?.company ?? "УК дома";
  const messages: IssueMessage[] = detail.messages.map((item) => ({
    id: item.id,
    author: item.kind === "official_uk" ? company : item.author_user_id === user.id ? "Вы" : "Житель дома",
    kind: item.kind,
    body: item.body,
    createdAt: item.created_at,
  }));
  const events: IssueEvent[] = detail.history.map((item) => ({
    id: item.id,
    label: issueEventLabel(item),
    note: item.note ?? undefined,
    createdAt: item.created_at,
  }));
  return {
    id: card.id,
    version: card.version,
    houseId: card.house_id,
    title: card.title,
    category: categoriesCache.find((item) => item.id === card.category_id)?.name ?? "Другое",
    description: detail.reports[0]?.raw_description ?? "",
    scope: {
      allHouse: card.scope_all_house,
      entrances: detail.targets.flatMap((item) => item.entrance_number == null ? [] : [item.entrance_number]),
      apartments: detail.targets.flatMap((item) => item.apartment_entrance_number == null || item.apartment_number == null ? [] : [{ entrance: item.apartment_entrance_number, number: item.apartment_number }]),
    },
    status: card.status,
    closeResult: card.close_result ?? undefined,
    currentNote: card.current_note ?? undefined,
    authorId: card.author_user_id,
    supportsCount: card.support_count,
    supportedByMe: Boolean(card.supported_by_me),
    botMuted: false,
    createdAt: card.created_at,
    updatedAt: card.updated_at,
    messages,
    events,
    reports: detail.reports.map((item) => item.raw_description),
    reportIds: detail.reports.map((item) => item.id),
  };
}

async function loadIssue(id: string): Promise<Issue> {
  const [user, detail] = await Promise.all([
    currentUserCache ? Promise.resolve(currentUserCache) : getCurrentUser(),
    requestJson<WireDetail>(query("/issues/get_card", { card_id: id })),
  ]);
  return toIssue(detail, user);
}

async function loadIssues(houses: House[]): Promise<Issue[]> {
  const lists = await Promise.all(houses.map((house) => requestJson<{ cards: WireCard[] }>(query("/issues/list_cards", { house_id: house.id, include_closed: "true" }))));
  const ids = [...new Set(lists.flatMap((item) => item.cards.map((card) => card.id)))];
  return Promise.all(ids.map(loadIssue));
}

function residentRequest(item: WireRequest): ResidentRequest {
  return {
    id: item.id,
    houseId: item.house_id ?? "",
    address: item.address_display ?? undefined,
    fullName: item.submitted_full_name ?? "",
    entrance: item.submitted_entrance_number ?? 0,
    apartment: item.submitted_apartment_number ?? 0,
    status: item.status,
    outcome: item.outcome ?? undefined,
    decisionNote: item.decision_note ?? undefined,
    decidedAt: item.decided_at ?? undefined,
    createdAt: item.created_at,
  };
}

function registrationRequest(item: WireRequest): CompanyRegistrationRequest {
  return {
    id: item.id,
    companyName: item.proposed_company_name ?? "",
    firstStaffPhone: item.phone_number ?? "",
    explanation: item.free_text ?? "",
    status: item.status,
    createdAt: item.created_at,
  };
}

function houseRequest(item: WireRequest): HouseAdditionRequest {
  return {
    id: item.id,
    registrationRequestId: item.registration_request_id ?? undefined,
    companyId: item.company_id ?? undefined,
    address: item.entered_address ?? "",
    explanation: item.free_text ?? undefined,
    status: item.status,
    createdAt: item.created_at,
  };
}

export const realIssuesClient: IssuesClient = {
  async getSnapshot(role) {
    const user = await getCurrentUser();
    currentUserCache = user;
    const categories = await requestJson<{ categories: IssueCategory[] }>("/issues/list_categories");
    categoriesCache = categories.categories;
    const houseMap = new Map<string, House>();
    let grants: ResidentGrant[] = [];
    let offers: ResidentOffer[] = [];
    let staff: StaffAssignment[] = [];
    let residentRequests: ResidentRequest[] = [];
    let companyRequests: CompanyRegistrationRequest[] = [];
    let houseRequests: HouseAdditionRequest[] = [];

    if (role === "resident") {
      const [grantResponse, offerResponse, residentResponse, companyResponse, houseResponse] = await Promise.all([
        requestJson<{ items: WireGrant[] }>("/access/list_grants"),
        requestJson<{ items: WireOffer[] }>("/access/list_offers"),
        requestJson<{ items: WireRequest[] }>(query("/access/list_requests", { request_kind: "resident" })),
        requestJson<{ items: WireRequest[] }>(query("/access/list_requests", { request_kind: "company_registration" })),
        requestJson<{ items: WireRequest[] }>(query("/access/list_requests", { request_kind: "house_addition" })),
      ]);
      grants = grantResponse.items.map((item) => {
        rememberHouse({ id: item.house_id, address: item.address_display, company: "УК дома" }, houseMap);
        return { id: item.id, houseId: item.house_id, fullName: user.full_name ?? "Житель", phone: user.phone_number ?? "", entrance: item.entrance_number, apartment: item.apartment_number, validUntil: item.valid_to ?? undefined, status: item.status, decidedBy: "УК дома", decidedAt: "" };
      });
      offers = offerResponse.items.map((item) => {
        rememberHouse({ id: item.house_id, address: item.address_display, company: "УК дома" }, houseMap);
        return { id: item.id, houseId: item.house_id, phone: user.phone_number ?? "", entrance: item.entrance_number, apartment: item.apartment_number, status: item.status, createdAt: item.created_at };
      });
      residentRequests = residentResponse.items.map(residentRequest);
      for (const item of residentResponse.items) {
        if (item.house_id && item.address_display) rememberHouse({ id: item.house_id, address: item.address_display, company: "УК дома" }, houseMap);
      }
      companyRequests = companyResponse.items.map(registrationRequest);
      houseRequests = houseResponse.items.map(houseRequest);
    } else {
      const memberships = user.staff_assignments;
      const responses = await Promise.all(memberships.map(async (membership) => {
        const company = membership.company_id;
        const [houseResponse, staffResponse, residentResponse, offerResponse] = await Promise.all([
          requestJson<{ items: { id: string; address_display: string; entrance_count: number | null }[] }>(query("/access/list_company_houses", { company_id: company })),
          requestJson<{ items: WireStaff[] }>(query("/access/list_staff", { company_id: company })),
          requestJson<{ items: WireResident[] }>(query("/access/list_residents", { company_id: company })),
          requestJson<{ items: WireCompanyOffer[] }>(query("/access/list_company_offers", { company_id: company })),
        ]);
        return { membership, houseResponse, staffResponse, residentResponse, offerResponse };
      }));
      for (const response of responses) {
        for (const item of response.houseResponse.items) rememberHouse({ id: item.id, address: item.address_display, company: response.membership.company_name, companyId: response.membership.company_id, entranceCount: item.entrance_count ?? undefined }, houseMap);
        staff.push(...response.staffResponse.items.filter((item) => !item.revoked_at).map((item) => ({ id: item.id, companyId: response.membership.company_id, phone: item.phone_number, rights: { manageStaff: item.can_manage_staff, manageResidents: item.can_manage_residents, manageIssues: item.can_manage_issues }, bound: Boolean(item.user_id), createdAt: item.created_at })));
        grants.push(...response.residentResponse.items.map((item) => ({ id: item.grant_id, houseId: item.house_id, fullName: item.full_name ?? "Житель", phone: item.phone_number ?? "", entrance: item.entrance_number, apartment: item.apartment_number, validUntil: item.valid_to ?? undefined, status: item.status, decidedBy: item.decided_by, decidedAt: item.decided_at ?? "" })));
        offers.push(...response.offerResponse.items.map((item) => ({ id: item.id, houseId: item.house_id, phone: item.phone_number, entrance: item.entrance_number, apartment: item.apartment_number, status: item.status, createdAt: item.created_at })));
      }
      const [residentResponse, houseResponse] = await Promise.all([
        requestJson<{ items: WireRequest[] }>(query("/access/list_requests", { request_kind: "resident" })),
        requestJson<{ items: WireRequest[] }>(query("/access/list_requests", { request_kind: "house_addition" })),
      ]);
      residentRequests = residentResponse.items.filter((item) => Boolean(item.house_id && houseMap.has(item.house_id))).map(residentRequest);
      houseRequests = houseResponse.items.map(houseRequest);
    }

    housesCache = [...houseMap.values()];
    const issueHouses = role === "resident"
      ? housesCache.filter((house) => grants.some((grant) => grant.houseId === house.id && grant.status === "active"))
      : housesCache;
    const issues = await loadIssues(issueHouses);
    lastSnapshot = { houses: housesCache, categories: categoriesCache, issues, residentRequests, residentGrants: grants, residentOffers: offers, staffAssignments: staff, companyRequests, houseRequests };
    return lastSnapshot;
  },

  async suggestIssue(input): Promise<IssueSuggestion> {
    const category = categoriesCache.find((item) => item.name === input.category);
    const response = await post<{ suggested_title: string; similar_card_ids: string[]; candidates: { id: string; title: string }[]; source: "groq" | "local" }>("/issues/suggest", {
      house_id: input.houseId,
      description: input.description.trim(),
      category_id: category?.id ?? null,
    });
    const visibleIds = new Set(response.candidates.map((item) => item.id));
    const similarIssues = await Promise.all(response.similar_card_ids.filter((id) => visibleIds.has(id)).map((id) =>
      lastSnapshot?.issues.find((item) => item.id === id) ?? loadIssue(id),
    ));
    return { suggestedTitle: response.suggested_title, similarIssues, source: response.source };
  },

  async createIssue(input, idempotencyKey) {
    const category = categoriesCache.find((item) => item.name === input.category);
    if (!category) throw new Error("Выбранная категория недоступна");
    const response = await post<{ card: WireCard }>("/issues/create_card", {
      house_id: input.houseId,
      category_id: category.id,
      title: input.title.trim(),
      description: input.description.trim(),
      scope_all_house: input.scope.allHouse,
      target_entrances: input.scope.allHouse ? [] : input.scope.entrances,
      target_apartments: input.scope.allHouse ? [] : input.scope.apartments.map((item) => ({ entrance_number: item.entrance, apartment_number: item.number })),
    }, idempotencyKey);
    return loadIssue(response.card.id);
  },

  async supportIssue(id, report, idempotencyKey) {
    const response = await post<{ report_id?: string | null }>("/issues/support_card", { card_id: id, description: report?.trim() || null }, idempotencyKey);
    return { issue: await loadIssue(id), reportId: response.report_id ?? undefined };
  },

  async addMessage(id, _role, body, idempotencyKey) {
    const response = await post<{ message: { id: string } }>("/issues/add_comment", { card_id: id, text: body.trim() }, idempotencyKey);
    return { ...(await loadIssue(id)), lastMessageId: response.message.id };
  },

  async getBotMute(subject: NotificationSubject, id: string) {
    const response = await requestJson<{ is_muted: boolean }>(query("/notifications/get_mute", { subject_kind: subject, subject_id: id }));
    return response.is_muted;
  },

  async setBotMute(subject: NotificationSubject, id: string, muted: boolean) {
    const response = await post<{ is_muted: boolean }>("/notifications/set_mute", { subject_kind: subject, subject_id: id, is_muted: muted });
    return response.is_muted;
  },

  async updateStatus(id, status, note, closeResult) {
    await post("/issues/set_status", { card_id: id, status, note: note.trim() || null, close_result: closeResult ?? null });
    return loadIssue(id);
  },

  async editIssue(id, expectedVersion, input) {
    const category = categoriesCache.find((item) => item.name === input.category);
    if (!category) throw new Error("Выбранная категория недоступна");
    await post("/issues/edit_card", {
      card_id: id,
      expected_version: expectedVersion,
      category_id: category.id,
      title: input.title.trim(),
      scope_all_house: input.scope.allHouse,
      target_entrances: input.scope.allHouse ? [] : input.scope.entrances,
      target_apartments: input.scope.allHouse ? [] : input.scope.apartments.map((item) => ({ entrance_number: item.entrance, apartment_number: item.number })),
    });
    return loadIssue(id);
  },

  async mergeIssues(leftId, rightId, finalTitle, finalStatus, finalNote) {
    const response = await post<{ card: WireCard }>("/issues/merge_cards", {
      left_id: leftId,
      right_id: rightId,
      final_title: finalTitle.trim(),
      final_status: finalStatus,
      final_note: finalNote.trim() || null,
    });
    return loadIssue(response.card.id);
  },

  async reopenIssue(id, reason) {
    await post("/issues/reopen_card", { card_id: id, comment: reason.trim() });
    return loadIssue(id);
  },

  async submitResidentRequest(input, idempotencyKey) {
    const response = await post<{ id: string; status: ResidentRequest["status"] }>("/access/create_resident_request", { house_id: input.houseId, full_name: input.fullName.trim(), entrance_number: input.entrance, apartment_number: input.apartment }, idempotencyKey);
    return { ...input, id: response.id, status: response.status, createdAt: new Date().toISOString() };
  },

  async decideResidentRequest(id, outcome, note) {
    await post("/access/decide_resident_request", { request_id: id, outcome, decision_note: note.trim() });
    return { id, houseId: "", fullName: "", entrance: 0, apartment: 0, status: "closed", outcome, decisionNote: note, createdAt: "" };
  },

  async createResidentOffer(input) {
    const response = await post<{ id: string; status: ResidentOffer["status"] }>("/access/create_resident_offer", { house_id: input.houseId, entrance_number: input.entrance, apartment_number: input.apartment, phone_number: input.phone });
    return { ...input, id: response.id, status: response.status, createdAt: new Date().toISOString() };
  },

  async answerResidentOffer(id, accept) {
    const response = await post<{ status: ResidentOffer["status"] }>("/access/respond_resident_offer", { offer_id: id, accept });
    return { id, houseId: "", phone: "", entrance: 0, apartment: 0, status: response.status, createdAt: "" };
  },

  async assignStaff(companyId, phone, rights) {
    const response = await post<{ id: string }>("/access/assign_staff", { company_id: companyId, phone_number: phone, can_manage_staff: rights.manageStaff, can_manage_residents: rights.manageResidents, can_manage_issues: rights.manageIssues });
    return { id: response.id, companyId, phone, rights, bound: false, createdAt: new Date().toISOString() };
  },

  async createCompanyRegistration(input) {
    const response = await post<{ id: string; status: string }>("/access/create_company_registration", { phone_number: input.firstStaffPhone, free_text: input.explanation.trim(), proposed_company_name: input.companyName.trim() });
    return { ...input, id: response.id, status: response.status, createdAt: new Date().toISOString() };
  },

  async createHouseRequest(address, registrationRequestId, explanation, companyId) {
    if (!registrationRequestId && !companyId) throw new Error("Выберите управляющую компанию");
    const response = await post<{ id: string; status: string }>("/access/create_house_request", { registration_request_id: registrationRequestId ?? null, company_id: registrationRequestId ? null : companyId, entered_address: address.trim(), free_text: explanation?.trim() || null });
    return { id: response.id, address, registrationRequestId, companyId, explanation, status: response.status, createdAt: new Date().toISOString() };
  },

  async searchHouses(text) {
    if (text.trim().length < 2) return [];
    const response = await requestJson<{ items: { id: string; address_display: string; entrance_count: number | null }[] }>(query("/access/search_houses", { text: text.trim() }));
    return response.items.map((item) => ({ id: item.id, address: item.address_display, company: "УК дома", entranceCount: item.entrance_count ?? undefined }));
  },
};
