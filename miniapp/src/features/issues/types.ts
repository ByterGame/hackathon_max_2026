export type Role = "resident" | "employee";

export type IssueStatus =
  | "open"
  | "reviewing"
  | "needs_info"
  | "in_progress"
  | "closed";

export type CloseResult = "solved" | "invalid";

export interface House {
  id: string;
  address: string;
  company: string;
  entranceCount?: number;
  companyId?: string;
}

export interface IssueCategory {
  id: string;
  code: string;
  name: string;
}

export interface IssueScope {
  allHouse: boolean;
  entrances: number[];
  apartments: { entrance: number; number: number }[];
}

export interface IssueMessage {
  id: string;
  author: string;
  kind: "resident_comment" | "official_uk";
  body: string;
  createdAt: string;
}

export interface IssueEvent {
  id: string;
  label: string;
  note?: string;
  createdAt: string;
}

export interface Issue {
  id: string;
  version?: number;
  houseId: string;
  title: string;
  category: string;
  description: string;
  scope: IssueScope;
  status: IssueStatus;
  closeResult?: CloseResult;
  currentNote?: string;
  authorId: string;
  supportsCount: number;
  supportedByMe: boolean;
  botMuted: boolean;
  createdAt: string;
  updatedAt: string;
  messages: IssueMessage[];
  lastMessageId?: string;
  events: IssueEvent[];
  reports: string[];
  reportIds?: string[];
}

export interface CreateIssueInput {
  houseId: string;
  title: string;
  category: string;
  description: string;
  scope: IssueScope;
}

export interface EditIssueInput {
  title: string;
  category: string;
  scope: IssueScope;
}

export interface ResidentRequest {
  id: string;
  houseId: string;
  address?: string;
  fullName: string;
  entrance: number;
  apartment: number;
  status: "open" | "reviewing" | "needs_info" | "closed" | "cancelled";
  outcome?: "granted" | "denied";
  decisionNote?: string;
  decidedAt?: string;
  createdAt: string;
  botMuted?: boolean;
}

export interface CompanyRegistrationRequest {
  id: string;
  companyName: string;
  firstStaffPhone: string;
  explanation: string;
  status: string;
  outcome?: "approved" | "rejected";
  decisionNote?: string;
  createdAt: string;
}

export interface HouseAdditionRequest {
  id: string;
  registrationRequestId?: string;
  companyId?: string;
  address: string;
  explanation?: string;
  status: string;
  outcome?: "approved" | "rejected";
  decisionNote?: string;
  createdAt: string;
}

export interface StaffRights {
  manageStaff: boolean;
  manageResidents: boolean;
  manageIssues: boolean;
}

export interface StaffAssignment {
  id: string;
  companyId?: string;
  phone: string;
  fullName?: string;
  rights: StaffRights;
  bound: boolean;
  createdAt: string;
}

export interface ResidentGrant {
  id: string;
  houseId: string;
  fullName: string;
  phone: string;
  entrance: number;
  apartment: number;
  validUntil?: string;
  status?: "active" | "expired" | "revoked";
  decidedBy: string;
  decidedAt: string;
}

export interface ResidentOffer {
  id: string;
  houseId: string;
  phone: string;
  entrance: number;
  apartment: number;
  status: "pending" | "accepted" | "declined" | "cancelled";
  createdAt: string;
}

export const issueStatusLabels: Record<IssueStatus, string> = {
  open: "Открыта",
  reviewing: "На рассмотрении",
  needs_info: "Нужны уточнения",
  in_progress: "В работе",
  closed: "Закрыта",
};

export const issueCategories = [
  "Лифт",
  "Вода и отопление",
  "Освещение",
  "Подъезд и двери",
  "Крыша и фасад",
  "Другое",
];

export const demoResident = {
  id: "resident-demo",
  name: "Алексей Петров",
  houseId: "pushkina-5",
  phone: "+79990000024",
  entrance: 2,
  apartment: 24,
};

export function formatIssueScope(scope: IssueScope): string {
  if (scope.allHouse) return "Весь дом";

  const parts = [
    ...scope.entrances.map((number) => `подъезд №${number}`),
    ...scope.apartments.map((item) => `квартира №${item.number} (подъезд №${item.entrance})`),
  ];
  return parts.join(", ") || "Область не указана";
}

export function formatDate(value: string): string {
  if (!value || Number.isNaN(new Date(value).getTime())) return "Дата не указана";
  return new Intl.DateTimeFormat("ru-RU", {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}
