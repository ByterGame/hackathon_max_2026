import {
  demoResident,
  type CloseResult,
  type CompanyRegistrationRequest,
  type CreateIssueInput,
  type EditIssueInput,
  type House,
  type HouseAdditionRequest,
  type Issue,
  type IssueCategory,
  type IssueScope,
  type IssueStatus,
  type ResidentGrant,
  type ResidentOffer,
  type ResidentRequest,
  type Role,
  type StaffAssignment,
  type StaffRights,
} from "../types";
import { realIssuesClient } from "./real_client_api";

// Локальное демо включается только через VITE_DEMO_MODE=true.
const STORAGE_KEY = "housing-miniapp-demo-v4";

export interface AppSnapshot {
  houses: House[];
  categories: IssueCategory[];
  issues: Issue[];
  residentRequests: ResidentRequest[];
  residentGrants: ResidentGrant[];
  residentOffers: ResidentOffer[];
  staffAssignments: StaffAssignment[];
  companyRequests: CompanyRegistrationRequest[];
  houseRequests: HouseAdditionRequest[];
}

export interface IssueSuggestion {
  suggestedTitle: string;
  summaryDescription: string | null;
  similarIssues: Issue[];
  source: "gigachat" | "local";
  descriptionCheck: "ok" | "warning" | "not_checked";
  descriptionWarning: string | null;
}

export interface SupportResult {
  issue: Issue;
  reportId?: string;
}

export type NotificationSubject = "issue_card" | "resident_request";

export interface IssuesClient {
  getSnapshot(role: Role): Promise<AppSnapshot>;
  getIssue(id: string, role: Role): Promise<Issue>;
  suggestIssue(input: CreateIssueInput): Promise<IssueSuggestion>;
  createIssue(input: CreateIssueInput, idempotencyKey?: string): Promise<Issue>;
  supportIssue(id: string, report?: string, idempotencyKey?: string): Promise<SupportResult>;
  addMessage(id: string, role: Role, body: string, idempotencyKey?: string): Promise<Issue>;
  getBotMute(subject: NotificationSubject, id: string): Promise<boolean>;
  setBotMute(subject: NotificationSubject, id: string, muted: boolean): Promise<boolean>;
  updateStatus(id: string, status: IssueStatus, note: string, closeResult?: CloseResult): Promise<Issue>;
  editIssue(id: string, expectedVersion: number, input: EditIssueInput): Promise<Issue>;
  mergeIssues(leftId: string, rightId: string, finalTitle: string, finalStatus: Exclude<IssueStatus, "closed">, finalNote: string): Promise<Issue>;
  reopenIssue(id: string, reason: string): Promise<Issue>;
  submitResidentRequest(input: Pick<ResidentRequest, "houseId" | "fullName" | "apartment"> & { entrance: number }, idempotencyKey?: string): Promise<ResidentRequest>;
  decideResidentRequest(id: string, outcome: "granted" | "denied", note: string): Promise<ResidentRequest>;
  createResidentOffer(input: Omit<ResidentOffer, "id" | "status" | "createdAt"> & { entrance: number }): Promise<ResidentOffer>;
  answerResidentOffer(id: string, accept: boolean): Promise<ResidentOffer>;
  assignStaff(companyId: string, phone: string, rights: StaffRights): Promise<StaffAssignment>;
  createCompanyRegistration(input: Pick<CompanyRegistrationRequest, "companyName" | "firstStaffPhone" | "explanation">): Promise<CompanyRegistrationRequest>;
  createHouseRequest(address: string, entranceCount: number, apartmentCount: number, registrationRequestId?: string, explanation?: string, companyId?: string): Promise<HouseAdditionRequest>;
  searchHouses(text: string): Promise<House[]>;
}

function timeAgo(hours: number): string {
  return new Date(Date.now() - hours * 60 * 60 * 1000).toISOString();
}

function makeId(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function seed(): AppSnapshot {
  const houses: House[] = [
    { id: "pushkina-5", address: "ул. Пушкина, д. 5", company: "УК «Дом-Сервис»", companyId: "demo-company", entranceCount: 4, apartmentCount: 120 },
    { id: "lenina-12", address: "ул. Ленина, д. 12", company: "УК «Дом-Сервис»", companyId: "demo-company", entranceCount: 3, apartmentCount: 90 },
  ];
  const issue = (
    id: string,
    houseId: string,
    title: string,
    category: string,
    description: string,
    scope: IssueScope,
    status: IssueStatus,
    supportsCount: number,
    hoursAgo: number,
    currentNote?: string,
  ): Issue => ({
    id,
    version: 1,
    houseId,
    title,
    category,
    description,
    summaryDescription: description,
    scope,
    status,
    currentNote,
    authorId: "another-resident",
    supportsCount,
    supportedByMe: false,
    botMuted: false,
    createdAt: timeAgo(hoursAgo + 2),
    updatedAt: timeAgo(hoursAgo),
    reports: [description],
    events: [
      { id: `${id}-created`, label: "Проблема создана", createdAt: timeAgo(hoursAgo + 2) },
      ...(status === "open" ? [] : [{ id: `${id}-status`, label: status === "closed" ? "Проблема закрыта" : "Статус обновлён", note: currentNote, createdAt: timeAgo(hoursAgo) }]),
    ],
    messages: currentNote
      ? [{ id: `${id}-official`, author: "УК «Дом-Сервис»", kind: "official_uk", body: currentNote, createdAt: timeAgo(hoursAgo) }]
      : [],
  });

  const issues: Issue[] = [
    issue("lift-1", "pushkina-5", "Не работает лифт", "Лифт", "Лифт в подъезде №2 не реагирует на вызов. Кабина стоит на первом этаже.", { allHouse: false, entrances: [2], apartments: [] }, "in_progress", 8, 1, "Лифтовая служба уведомлена. Специалист направлен на объект."),
    issue("water-1", "pushkina-5", "Нет холодной воды", "Водоснабжение", "С утра в доме нет холодной воды. Давление отсутствует во всех подъездах.", { allHouse: true, entrances: [], apartments: [] }, "reviewing", 14, 6, "Проверяем причину отключения с ресурсоснабжающей организацией."),
    issue("light-1", "pushkina-5", "Не горит свет в подъезде", "Электричество", "На пятом этаже подъезда №2 не работает освещение.", { allHouse: false, entrances: [2], apartments: [] }, "open", 3, 3),
    issue("door-1", "pushkina-5", "Не закрывается входная дверь", "Другое", "Дверь в подъезде №2 закрывается неплотно. Похоже, сломан доводчик.", { allHouse: false, entrances: [2], apartments: [] }, "closed", 5, 48, "Доводчик заменён. Дверь закрывается штатно."),
    issue("roof-1", "lenina-12", "Протекает крыша", "Другое", "На последнем этаже заметны следы протечки после дождя.", { allHouse: false, entrances: [1], apartments: [] }, "needs_info", 4, 4, "Уточните, пожалуйста, место протечки."),
    issue("lift-2", "lenina-12", "Лифт останавливается между этажами", "Лифт", "Лифт в подъезде №3 останавливается на несколько минут между этажами.", { allHouse: false, entrances: [3], apartments: [] }, "open", 6, 7),
  ];
  issues[3].closeResult = "solved";
  return {
    houses,
    categories: [
      { id: "demo-elevator", code: "elevator", name: "Лифт" },
      { id: "demo-water", code: "water", name: "Водоснабжение" },
      { id: "demo-electricity", code: "electricity", name: "Электричество" },
      { id: "demo-other", code: "other", name: "Другое" },
    ],
    issues,
    residentRequests: [],
    residentGrants: [{ id: "grant-demo", apartmentId: "demo-apartment-24", houseId: "pushkina-5", fullName: demoResident.name, phone: demoResident.phone, entrance: 2, apartment: 24, decidedBy: "УК «Дом-Сервис»", decidedAt: timeAgo(720) }],
    residentOffers: [],
    staffAssignments: [{ id: "staff-demo", companyId: "demo-company", phone: "+79990000001", fullName: "Мария Иванова", rights: { manageStaff: true, manageResidents: true, manageIssues: true }, bound: true, createdAt: timeAgo(1440) }],
    companyRequests: [],
    houseRequests: [],
  };
}

function load(): AppSnapshot {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (raw) return JSON.parse(raw) as AppSnapshot;
  } catch {
    // Демо продолжит работать без localStorage.
  }
  return seed();
}

let state: AppSnapshot | undefined;

function current(): AppSnapshot {
  state ??= load();
  return state;
}

function save(): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(current()));
  } catch {
    // Переполненное или отключённое хранилище не блокирует текущую сессию.
  }
}

function visibleToResident(issue: Issue): boolean {
  const grants = current().residentGrants.filter((grant) =>
    grant.phone === demoResident.phone
    && grant.houseId === issue.houseId
    && (!grant.validUntil || new Date(grant.validUntil).getTime() > Date.now()),
  );
  if (!grants.length) return false;
  if (issue.authorId === demoResident.id || issue.scope.allHouse) return true;
  return grants.some((grant) => (grant.entrance !== undefined && issue.scope.entrances.includes(grant.entrance))
    || issue.scope.apartments.some((item) => item.number === grant.apartment && (item.entrance === undefined || item.entrance === grant.entrance)));
}

function normalizePhone(value: string): string {
  const digits = value.replace(/\D/g, "");
  if (digits.length === 11 && (digits.startsWith("7") || digits.startsWith("8"))) return `+7${digits.slice(1)}`;
  throw new Error("Введите номер в формате +7 999 123-45-67");
}

function addGrant(houseId: string, apartment: number, fullName: string, phone: string, entrance: number): ResidentGrant {
  const existing = current().residentGrants.find((grant) => grant.houseId === houseId && grant.apartment === apartment && grant.phone === phone);
  if (existing) return existing;
  const grant: ResidentGrant = { id: makeId(), apartmentId: makeId(), houseId, entrance, apartment, fullName, phone, decidedBy: "УК «Дом-Сервис»", decidedAt: new Date().toISOString() };
  current().residentGrants.push(grant);
  return grant;
}

function getIssue(id: string): Issue {
  const issue = current().issues.find((item) => item.id === id);
  if (!issue) throw new Error("Проблема не найдена");
  return issue;
}

function assertText(value: string, label: string): string {
  const trimmed = value.trim();
  if (!trimmed) throw new Error(`Укажите ${label}`);
  return trimmed;
}

const demoIssuesClient: IssuesClient = {
  async getSnapshot(role) {
    const data = current();
    return {
      houses: data.houses.map((item) => ({ ...item })),
      categories: data.categories.map((item) => ({ ...item })),
      issues: data.issues.filter((item) => role === "employee" || visibleToResident(item)).map((item) => structuredClone(item)),
      residentRequests: data.residentRequests.map((item) => ({ ...item })),
      residentGrants: data.residentGrants.map((item) => ({ ...item })),
      residentOffers: data.residentOffers.map((item) => ({ ...item })),
      staffAssignments: data.staffAssignments.map((item) => structuredClone(item)),
      companyRequests: data.companyRequests.map((item) => ({ ...item })),
      houseRequests: data.houseRequests.map((item) => ({ ...item })),
    };
  },

  async getIssue(id, role) {
    const issue = getIssue(id);
    if (role === "resident" && !visibleToResident(issue)) throw new Error("Проблема недоступна");
    return structuredClone(issue);
  },

  async suggestIssue(input) {
    const similarIssues = current().issues.filter((issue) =>
      issue.houseId === input.houseId
      && issue.status !== "closed"
      && visibleToResident(issue)
      && issue.category === input.category,
    ).slice(0, 3).map((issue) => structuredClone(issue));
    return {
      suggestedTitle: input.description.trim().split(/[.!?\n]/)[0].slice(0, 100),
      summaryDescription: null,
      similarIssues,
      source: "local",
      descriptionCheck: "not_checked",
      descriptionWarning: null,
    };
  },

  async createIssue(input) {
    const title = assertText(input.title, "краткую формулировку");
    const description = assertText(input.description, "описание проблемы");
    const summaryDescription = assertText(input.summaryDescription ?? description, "сводное описание проблемы");
    const grants = current().residentGrants.filter((item) => item.houseId === input.houseId && item.phone === demoResident.phone && (!item.validUntil || new Date(item.validUntil).getTime() > Date.now()));
    if (!grants.length) throw new Error("Нет действующего доступа к дому");
    const grant = input.apartmentId ? grants.find((item) => item.apartmentId === input.apartmentId) : grants.length === 1 ? grants[0] : undefined;
    if (input.scopeLevel !== "house" && !grant) throw new Error("Выберите свою квартиру для этой проблемы");
    if (input.scopeLevel === "entrance" && !grant?.entrance) throw new Error("В доступе к дому не указан подъезд. Уточните адрес у УК.");
    const scope: IssueScope = input.scopeLevel === "house"
      ? { allHouse: true, entrances: [], apartments: [] }
      : input.scopeLevel === "entrance"
        ? { allHouse: false, entrances: [grant!.entrance!], apartments: [] }
        : { allHouse: false, entrances: [], apartments: [{ number: grant!.apartment, entrance: grant!.entrance }] };
    const now = new Date().toISOString();
    const issue: Issue = {
      id: makeId(),
      version: 1,
      houseId: input.houseId,
      title,
      description,
      summaryDescription,
      category: input.category,
      scope,
      status: "open",
      authorId: demoResident.id,
      supportsCount: 1,
      supportedByMe: true,
      botMuted: false,
      createdAt: now,
      updatedAt: now,
      reports: [description],
      messages: [],
      events: [{ id: makeId(), label: "Проблема создана", createdAt: now }],
    };
    current().issues.unshift(issue);
    save();
    return structuredClone(issue);
  },

  async supportIssue(id, report) {
    const issue = getIssue(id);
    if (!visibleToResident(issue)) throw new Error("Нет доступа к этой проблеме");
    if (issue.status === "closed") throw new Error("Закрытую проблему нельзя поддержать");
    if (!issue.supportedByMe) {
      issue.supportedByMe = true;
      issue.supportsCount += 1;
      issue.updatedAt = new Date().toISOString();
    }
    if (report?.trim() && !issue.reports.includes(report.trim())) issue.reports.push(report.trim());
    save();
    return { issue: structuredClone(issue), reportId: report?.trim() ? `${id}-demo-report` : undefined };
  },

  async addMessage(id, role, body) {
    const issue = getIssue(id);
    if (role === "resident" && !visibleToResident(issue)) throw new Error("Нет доступа к этой проблеме");
    const now = new Date().toISOString();
    issue.messages.push({
      id: makeId(),
      author: role === "employee" ? "УК «Дом-Сервис»" : "Вы",
      kind: role === "employee" ? "official_uk" : "resident_comment",
      body: assertText(body, "текст сообщения"),
      createdAt: now,
    });
    issue.updatedAt = now;
    save();
    return { ...structuredClone(issue), lastMessageId: issue.messages[issue.messages.length - 1]?.id };
  },

  async getBotMute(subject, id) {
    if (subject === "issue_card") return getIssue(id).botMuted;
    return Boolean(current().residentRequests.find((item) => item.id === id)?.botMuted);
  },

  async setBotMute(subject, id, muted) {
    if (subject === "issue_card") getIssue(id).botMuted = muted;
    else {
      const request = current().residentRequests.find((item) => item.id === id);
      if (!request) throw new Error("Заявка не найдена");
      request.botMuted = muted;
    }
    save();
    return muted;
  },

  async updateStatus(id, status, note, closeResult) {
    const issue = getIssue(id);
    if (issue.status === "closed" && status !== "closed") throw new Error("Переоткрыть карточку может только её автор");
    if (status === "closed" && (!closeResult || !note.trim())) {
      throw new Error("Для закрытия выберите результат и укажите пояснение");
    }
    const now = new Date().toISOString();
    issue.status = status;
    issue.closeResult = status === "closed" ? closeResult : undefined;
    issue.currentNote = note.trim() || issue.currentNote;
    issue.updatedAt = now;
    issue.version = (issue.version ?? 1) + 1;
    issue.events.push({ id: makeId(), label: status === "closed" ? "Проблема закрыта" : "Статус обновлён", note: note.trim() || undefined, createdAt: now });
    if (note.trim()) {
      issue.messages.push({ id: makeId(), author: "УК «Дом-Сервис»", kind: "official_uk", body: note.trim(), createdAt: now });
    }
    save();
    return structuredClone(issue);
  },

  async editIssue(id, expectedVersion, input) {
    const issue = getIssue(id);
    if ((issue.version ?? 1) !== expectedVersion) throw new Error("Карточка изменилась; обновите её");
    issue.title = assertText(input.title, "название проблемы");
    issue.summaryDescription = assertText(input.summaryDescription, "сводное описание проблемы");
    issue.category = input.category;
    issue.scope = structuredClone(input.scope);
    issue.version = expectedVersion + 1;
    issue.updatedAt = new Date().toISOString();
    issue.events.push({ id: makeId(), label: "Карточка обновлена", createdAt: issue.updatedAt });
    save();
    return structuredClone(issue);
  },

  async mergeIssues(leftId, rightId, finalTitle, finalStatus, finalNote) {
    const left = getIssue(leftId);
    const right = getIssue(rightId);
    if (left.houseId !== right.houseId || left.status === "closed" || right.status === "closed") throw new Error("Объединить можно только открытые проблемы одного дома");
    left.title = assertText(finalTitle, "название");
    left.status = finalStatus;
    left.currentNote = finalNote.trim() || undefined;
    left.supportsCount += right.supportsCount;
    left.reports.push(...right.reports);
    left.messages.push(...right.messages);
    left.version = (left.version ?? 1) + 1;
    left.updatedAt = new Date().toISOString();
    left.events.push({ id: makeId(), label: "Проблемы объединены", note: right.title, createdAt: left.updatedAt });
    current().issues = current().issues.filter((item) => item.id !== rightId);
    save();
    return structuredClone(left);
  },

  async reopenIssue(id, reason) {
    const issue = getIssue(id);
    if (issue.authorId !== demoResident.id || issue.status !== "closed") throw new Error("Переоткрыть может только автор закрытой проблемы");
    const now = new Date().toISOString();
    const body = assertText(reason, "причину переоткрытия");
    issue.status = "open";
    issue.closeResult = undefined;
    issue.currentNote = undefined;
    issue.updatedAt = now;
    issue.events.push({ id: makeId(), label: "Проблема переоткрыта", note: body, createdAt: now });
    issue.messages.push({ id: makeId(), author: "Вы", kind: "resident_comment", body, createdAt: now });
    save();
    return structuredClone(issue);
  },

  async submitResidentRequest(input) {
    const house = current().houses.find((item) => item.id === input.houseId);
    if (!house) throw new Error("Дом не найден");
    if (!Number.isInteger(input.entrance) || input.entrance < 1 || (house.entranceCount && input.entrance > house.entranceCount)) throw new Error("Укажите корректный номер подъезда");
    if (!Number.isInteger(input.apartment) || input.apartment < 1) throw new Error("Укажите корректный номер квартиры");
    const request: ResidentRequest = {
      ...input,
      address: current().houses.find((house) => house.id === input.houseId)?.address,
      fullName: assertText(input.fullName, "ФИО"),
      id: makeId(),
      status: "open",
      createdAt: new Date().toISOString(),
    };
    current().residentRequests.unshift(request);
    save();
    return { ...request };
  },

  async decideResidentRequest(id, outcome, note) {
    const request = current().residentRequests.find((item) => item.id === id);
    if (!request) throw new Error("Заявка не найдена");
    if (request.status === "closed") throw new Error("По заявке уже принято решение");
    request.decisionNote = assertText(note, "пояснение к решению");
    request.outcome = outcome;
    request.status = "closed";
    request.decidedAt = new Date().toISOString();
    if (outcome === "granted") {
      if (!request.entrance) throw new Error("Перед выдачей доступа укажите подъезд жильца");
      addGrant(request.houseId, request.apartment, request.fullName, demoResident.phone, request.entrance);
    }
    save();
    return { ...request };
  },

  async createResidentOffer(input) {
    const house = current().houses.find((item) => item.id === input.houseId);
    if (!house) throw new Error("Дом не найден");
    if (!Number.isInteger(input.entrance) || input.entrance < 1 || (house.entranceCount && input.entrance > house.entranceCount)) throw new Error("Укажите корректный номер подъезда");
    if (!Number.isInteger(input.apartment) || input.apartment < 1) throw new Error("Укажите корректный номер квартиры");
    const offer: ResidentOffer = { ...input, phone: normalizePhone(input.phone), id: makeId(), status: "pending", createdAt: new Date().toISOString() };
    current().residentOffers.unshift(offer);
    save();
    return { ...offer };
  },

  async answerResidentOffer(id, accept) {
    const offer = current().residentOffers.find((item) => item.id === id);
    if (!offer || offer.phone !== demoResident.phone) throw new Error("Предложение не найдено для вашего номера");
    if (offer.status !== "pending") throw new Error("На предложение уже ответили");
    offer.status = accept ? "accepted" : "declined";
    if (accept) {
      if (!offer.entrance) throw new Error("В предложении не указан подъезд. Попросите УК создать новое предложение.");
      addGrant(offer.houseId, offer.apartment, demoResident.name, demoResident.phone, offer.entrance);
    }
    save();
    return { ...offer };
  },

  async assignStaff(companyId, phone, rights) {
    const normalized = normalizePhone(phone);
    const existing = current().staffAssignments.find((item) => item.companyId === companyId && item.phone === normalized);
    if (existing) {
      existing.rights = { ...rights };
      save();
      return structuredClone(existing);
    }
    const assignment: StaffAssignment = { id: makeId(), companyId, phone: normalized, rights: { ...rights }, bound: false, createdAt: new Date().toISOString() };
    current().staffAssignments.push(assignment);
    save();
    return structuredClone(assignment);
  },

  async createCompanyRegistration(input) {
    const request: CompanyRegistrationRequest = {
      id: makeId(),
      companyName: assertText(input.companyName, "название УК"),
      firstStaffPhone: normalizePhone(input.firstStaffPhone),
      explanation: assertText(input.explanation, "пояснение для поддержки"),
      status: "open",
      createdAt: new Date().toISOString(),
    };
    current().companyRequests.unshift(request);
    save();
    return { ...request };
  },

  async createHouseRequest(address, entranceCount, apartmentCount, registrationRequestId, explanation, companyId) {
    if (!Number.isInteger(entranceCount) || entranceCount < 1 || !Number.isInteger(apartmentCount) || apartmentCount < 1) {
      throw new Error("Укажите положительное количество подъездов и квартир");
    }
    const request: HouseAdditionRequest = {
      id: makeId(),
      registrationRequestId,
      companyId,
      address: assertText(address, "адрес дома"),
      entranceCount,
      apartmentCount,
      explanation: explanation?.trim(),
      status: "open",
      createdAt: new Date().toISOString(),
    };
    current().houseRequests.unshift(request);
    save();
    return { ...request };
  },

  async searchHouses(text) {
    const query = text.trim().toLowerCase();
    if (query.length < 2) return [];
    return current().houses.filter((house) => house.address.toLowerCase().includes(query)).map((house) => ({ ...house }));
  },
};

export const isDemoMode = import.meta.env.VITE_DEMO_MODE === "true";
export const issuesClient: IssuesClient = isDemoMode ? demoIssuesClient : realIssuesClient;
