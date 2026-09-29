import { useState } from "react";

import { accessRequestStatusLabels, type AccessRequestKind, type AccessRequestStatus } from "../features/issues/integrations/access_actions_api";
import { isDemoMode } from "../features/issues/integrations/client_api";
import { formatDate, type CompanyRegistrationRequest, type HouseAdditionRequest, type ResidentRequest } from "../features/issues/types";
import { NotificationToggle } from "../features/notifications/ui/NotificationToggle";
import { ScreenHeader } from "../shared/common_ui/ScreenHeader";
import { AccessCasePanel } from "./EmployeeAccess";

export interface ApplicantCase {
  kind: AccessRequestKind;
  id: string;
}

type RequestSummary = ApplicantCase & {
  title: string;
  description: string;
  status: string;
  outcome?: string;
  decisionNote?: string;
  createdAt: string;
};

interface RequestLists {
  residentRequests: ResidentRequest[];
  companyRequests: CompanyRegistrationRequest[];
  houseRequests: HouseAdditionRequest[];
}

function summaries({ residentRequests, companyRequests, houseRequests }: RequestLists): RequestSummary[] {
  return [
    ...residentRequests.map((item) => ({ kind: "resident" as const, id: item.id, title: item.address ?? "Дом по заявке", description: `Доступ к дому · подъезд №${item.entrance}, кв. ${item.apartment}`, status: item.status, outcome: item.outcome, decisionNote: item.decisionNote, createdAt: item.createdAt })),
    ...companyRequests.map((item) => ({ kind: "company_registration" as const, id: item.id, title: item.companyName || "Регистрация УК", description: "Регистрация управляющей компании", status: item.status, outcome: item.outcome, decisionNote: item.decisionNote, createdAt: item.createdAt })),
    ...houseRequests.map((item) => ({ kind: "house_addition" as const, id: item.id, title: item.address, description: "Подключение дома", status: item.status, outcome: item.outcome, decisionNote: item.decisionNote, createdAt: item.createdAt })),
  ].sort((a, b) => b.createdAt.localeCompare(a.createdAt));
}

function statusLabel(request: RequestSummary): string {
  if (request.status === "closed" && request.outcome === "granted") return "Доступ выдан";
  if (request.status === "closed" && request.outcome === "approved") return "Одобрена";
  if (request.status === "closed" && (request.outcome === "denied" || request.outcome === "rejected")) return "Отказано";
  return accessRequestStatusLabels[request.status as AccessRequestStatus] ?? request.status;
}

export function ApplicantRequestsList({ residentRequests, companyRequests, houseRequests, onOpen, limit, selectedCase, onChanged }: RequestLists & { onOpen: (item: ApplicantCase) => void; limit?: number; selectedCase?: ApplicantCase | null; onChanged?: () => Promise<void> }) {
  const requests = summaries({ residentRequests, companyRequests, houseRequests });
  if (requests.length === 0) return null;
  return <section className="requests-list">
    <div className="section-heading"><h2>Мои заявки</h2><span className="count-badge">{requests.length}</span></div>
    {requests.slice(0, limit).map((request) => {
      const expanded = selectedCase?.kind === request.kind && selectedCase.id === request.id;
      return <article className="panel request-card" key={`${request.kind}:${request.id}`}>
        <strong>{request.title}</strong>
        <span>{request.description}</span>
        <small>{formatDate(request.createdAt)} · {statusLabel(request)}</small>
        {request.decisionNote && <span>Пояснение: {request.decisionNote}</span>}
        {request.kind === "resident" && <NotificationToggle subject="resident_request" id={request.id} />}
        <button type="button" className="button button--soft" aria-expanded={onChanged ? expanded : undefined} onClick={() => onOpen({ kind: request.kind, id: request.id })}>{expanded ? "Скрыть обсуждение" : isDemoMode ? "Открыть заявку" : "Открыть заявку и обсуждение"}</button>
        {expanded && onChanged && <AccessCasePanel key={`${request.kind}:${request.id}`} kind={request.kind} id={request.id} perspective="applicant" onChanged={onChanged} />}
      </article>;
    })}
  </section>;
}

export function MyRequests({ residentRequests, companyRequests, houseRequests, initialCase, onBack, onChanged }: RequestLists & { initialCase: ApplicantCase | null; onBack: () => void; onChanged: () => Promise<void> }) {
  const [selectedCase, setSelectedCase] = useState<ApplicantCase | null>(initialCase);
  const requests = summaries({ residentRequests, companyRequests, houseRequests });
  const selectedIsListed = selectedCase ? requests.some((item) => item.kind === selectedCase.kind && item.id === selectedCase.id) : false;

  return <div className="page page--form">
    <ScreenHeader title="Мои заявки" subtitle="Статус и переписка" onBack={onBack} />
    {requests.length === 0 && !selectedCase && <div className="panel empty-state"><strong>Заявок пока нет</strong><p>Поданные заявки появятся здесь.</p></div>}
    {selectedCase && !selectedIsListed && <section className="panel request-card"><strong>Поданная заявка</strong><p className="muted-text">Открываем карточку. Список заявок обновится позже.</p><AccessCasePanel key={`${selectedCase.kind}:${selectedCase.id}`} kind={selectedCase.kind} id={selectedCase.id} perspective="applicant" onChanged={onChanged} /></section>}
    <ApplicantRequestsList residentRequests={residentRequests} companyRequests={companyRequests} houseRequests={houseRequests} selectedCase={selectedCase} onChanged={onChanged} onOpen={(request) => setSelectedCase((current) => current?.kind === request.kind && current.id === request.id ? null : request)} />
  </div>;
}
