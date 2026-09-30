import { useEffect, useState } from "react";

import { accessRequestStatusLabels, listAccessRequestsPage, type AccessRequestDetail, type AccessRequestKind, type AccessRequestStatus, type AccessRequestsPage } from "../features/issues/integrations/access_actions_api";
import { isDemoMode } from "../features/issues/integrations/client_api";
import { formatApartmentLocation, formatDate, formatHouseCounts, type CompanyRegistrationRequest, type HouseAdditionRequest, type ResidentRequest } from "../features/issues/types";
import { NotificationToggle } from "../features/notifications/ui/NotificationToggle";
import { HttpError } from "../shared/base_http_client";
import { Icon } from "../shared/common_ui/Icon";
import { ScreenHeader } from "../shared/common_ui/ScreenHeader";
import { AccessCasePanel } from "./EmployeeAccess";
import { CancelResidentRequestControl } from "./CancelResidentRequestControl";
import "./my-requests.css";

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
  discussionPreview?: string;
  createdAt: string;
};

interface RequestLists {
  residentRequests: ResidentRequest[];
  companyRequests: CompanyRegistrationRequest[];
  houseRequests: HouseAdditionRequest[];
}

const PAGE_SIZE = 20;

function summaries({ residentRequests, companyRequests, houseRequests }: RequestLists): RequestSummary[] {
  return [
    ...residentRequests.map((item) => ({ kind: "resident" as const, id: item.id, title: item.address ?? "Дом по заявке", description: formatApartmentLocation(item.apartment, item.entrance).replace(", подъезд", " · подъезд"), status: item.status, outcome: item.outcome, decisionNote: item.decisionNote, createdAt: item.createdAt })),
    ...companyRequests.map((item) => ({ kind: "company_registration" as const, id: item.id, title: item.companyName || "Регистрация УК", description: "Регистрация управляющей компании", status: item.status, outcome: item.outcome, decisionNote: item.decisionNote, createdAt: item.createdAt })),
    ...houseRequests.map((item) => ({ kind: "house_addition" as const, id: item.id, title: item.address, description: formatHouseCounts(item.entranceCount, item.apartmentCount), status: item.status, outcome: item.outcome, decisionNote: item.decisionNote, createdAt: item.createdAt })),
  ].sort((a, b) => b.createdAt.localeCompare(a.createdAt));
}

function summaryFromPage(item: AccessRequestDetail): RequestSummary {
  const lastMessage = item.discussion?.at(-1);
  const author = lastMessage?.author_user_id === item.applicant_user_id
    ? "Вы"
    : lastMessage?.author_kind === "employee" ? "УК" : lastMessage?.author_kind === "support" ? "Поддержка" : "Участник";
  const common = { kind: item.kind, id: item.id, status: item.status, outcome: item.outcome ?? undefined, decisionNote: item.decision_note ?? undefined, discussionPreview: lastMessage ? `${author}: ${lastMessage.text}` : undefined, createdAt: item.created_at };
  if (item.kind === "resident") return {
    ...common,
    title: item.address_display ?? "Дом по заявке",
    description: formatApartmentLocation(item.submitted_apartment_number ?? 0, item.submitted_entrance_number ?? undefined).replace(", подъезд", " · подъезд"),
  };
  if (item.kind === "company_registration") return {
    ...common,
    title: item.proposed_company_name || "Регистрация УК",
    description: "Регистрация управляющей компании",
  };
  return {
    ...common,
    title: item.entered_address || "Подключение дома",
    description: formatHouseCounts(item.entrance_count, item.apartment_count),
  };
}

function statusLabel(request: RequestSummary): string {
  if (request.status === "closed" && request.outcome === "granted") return "Доступ выдан";
  if (request.status === "closed" && request.outcome === "approved") return "Одобрена";
  if (request.status === "closed" && (request.outcome === "denied" || request.outcome === "rejected")) return "Отказано";
  return accessRequestStatusLabels[request.status as AccessRequestStatus] ?? request.status;
}

function statusTone(request: RequestSummary): string {
  if (request.status === "closed") {
    if (request.outcome === "granted" || request.outcome === "approved") return "approved";
    if (request.outcome === "denied" || request.outcome === "rejected") return "denied";
  }
  return request.status;
}

function statusExplanation(request: RequestSummary): string | null {
  if (request.kind !== "resident") return null;
  if (request.status === "reviewing") return "Заявка проверяется управляющей компанией. После подтверждения откроется доступ к проблемам дома и ответам УК.";
  if (request.status === "needs_info") return "Для проверки нужны уточнения. Откройте обсуждение заявки и ответьте управляющей компании.";
  if (request.status === "open") return "Заявка отправлена управляющей компании и ожидает рассмотрения.";
  if (request.status === "cancelled") return "Заявка отменена.";
  if (request.outcome === "granted") return "Доступ к проблемам дома и ответам УК открыт.";
  if (request.outcome === "denied") return "В доступе отказано. Проверьте пояснение к решению и обсуждение заявки.";
  return null;
}

export function ApplicantRequestsList({ residentRequests, companyRequests, houseRequests, onOpen, limit, selectedCase, onChanged, pageItems, totalCount, fullPage = false }: RequestLists & { onOpen: (item: ApplicantCase) => void; limit?: number; selectedCase?: ApplicantCase | null; onChanged?: () => Promise<void>; pageItems?: RequestSummary[]; totalCount?: number; fullPage?: boolean }) {
  const requests = pageItems ?? summaries({ residentRequests, companyRequests, houseRequests });
  if (requests.length === 0) return null;

  return <section className="requests-list">
    <div className="section-heading"><h2>Мои заявки</h2><span className="count-badge">{totalCount ?? requests.length}</span></div>
    {requests.slice(0, limit).map((request) => {
      const expanded = selectedCase?.kind === request.kind && selectedCase.id === request.id;
      const explanation = fullPage ? statusExplanation(request) : null;
      if (fullPage) return <div className="my-requests__item" key={`${request.kind}:${request.id}`}>
        <article className="panel request-card my-requests__card">
          <strong className="my-requests__address">{request.title}</strong>
          <span className="my-requests__location">{request.description}</span>
          <span className={`my-requests__status my-requests__status--${statusTone(request)}`}>{statusLabel(request)}</span>
          {explanation && <p className="my-requests__explanation">{explanation}</p>}
          {request.decisionNote && <p className="my-requests__decision-note">Пояснение: {request.decisionNote}</p>}
          <small className="my-requests__date">Подана {formatDate(request.createdAt)}</small>
        </article>
        {request.kind === "resident" && <div className="my-requests__notifications"><NotificationToggle subject="resident_request" id={request.id} /></div>}
        <div className="my-requests__discussion">
          <button type="button" className="my-requests__discussion-toggle" aria-expanded={expanded} onClick={() => onOpen({ kind: request.kind, id: request.id })}>
            <strong>Обсуждение заявки</strong>
            <span>{request.discussionPreview ?? "Сообщений пока нет. Откройте заявку, чтобы написать управляющей компании."}</span>
            <Icon name="chevron" size={18} />
          </button>
          {expanded && onChanged && <AccessCasePanel key={`${request.kind}:${request.id}`} kind={request.kind} id={request.id} perspective="applicant" onChanged={onChanged} />}
        </div>
        {!isDemoMode && onChanged && request.kind === "resident" && request.status === "open" && !expanded && <CancelResidentRequestControl id={request.id} onChanged={onChanged} />}
      </div>;
      return <article className="panel request-card" key={`${request.kind}:${request.id}`}>
        <strong>{request.title}</strong>
        <span>{request.description}</span>
        <small>{formatDate(request.createdAt)} · {statusLabel(request)}</small>
        {request.decisionNote && <span>Пояснение: {request.decisionNote}</span>}
        {request.kind === "resident" && <NotificationToggle subject="resident_request" id={request.id} />}
        <button type="button" className="button button--soft" aria-expanded={onChanged ? expanded : undefined} onClick={() => onOpen({ kind: request.kind, id: request.id })}>{expanded ? "Скрыть обсуждение" : isDemoMode ? "Открыть заявку" : "Открыть заявку и обсуждение"}</button>
        {!isDemoMode && onChanged && request.kind === "resident" && request.status === "open" && !expanded && <CancelResidentRequestControl id={request.id} onChanged={onChanged} />}
        {expanded && onChanged && <AccessCasePanel key={`${request.kind}:${request.id}`} kind={request.kind} id={request.id} perspective="applicant" onChanged={onChanged} />}
      </article>;
    })}
  </section>;
}

export function MyRequests({ residentRequests, companyRequests, houseRequests, initialCase, onBack, onChanged }: RequestLists & { initialCase: ApplicantCase | null; onBack: () => void; onChanged: () => Promise<void> }) {
  const [selectedCase, setSelectedCase] = useState<ApplicantCase | null>(initialCase);
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<AccessRequestsPage | null>(null);
  const [loading, setLoading] = useState(!isDemoMode);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const demoRequests = summaries({ residentRequests, companyRequests, houseRequests });
  const requests = isDemoMode ? demoRequests.slice(offset, offset + PAGE_SIZE) : page?.items.map(summaryFromPage) ?? [];
  const total = isDemoMode ? demoRequests.length : page?.total ?? 0;
  const selectedIsListed = selectedCase ? requests.some((item) => item.kind === selectedCase.kind && item.id === selectedCase.id) : false;

  useEffect(() => {
    if (isDemoMode) return;
    let cancelled = false;
    setLoading(true); setError(""); setPage(null);
    void listAccessRequestsPage("mine", offset, PAGE_SIZE)
      .then((result) => { if (!cancelled) setPage(result); })
      .catch((reason) => { if (!cancelled) setError(reason instanceof HttpError && reason.status === 403 ? "Нет доступа к своим заявкам" : reason instanceof Error ? reason.message : "Не удалось загрузить заявки"); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [offset, revision]);

  async function refreshPage() {
    await onChanged();
    setRevision((current) => current + 1);
  }

  return <div className="page page--form my-requests">
    <ScreenHeader title="Мои заявки" subtitle="Статус и переписка" onBack={onBack} />
    {loading && <p className="muted-text">Загружаем заявки…</p>}
    {error && <p className="form-error" role="alert">{error}</p>}
    {!loading && !error && total === 0 && !selectedCase && <div className="panel empty-state"><strong>Заявок пока нет</strong><p>Поданные заявки появятся здесь.</p></div>}
    {selectedCase && !selectedIsListed && <section className="panel request-card"><strong>Поданная заявка</strong><p className="muted-text">Она не находится на текущей странице списка.</p><AccessCasePanel key={`${selectedCase.kind}:${selectedCase.id}`} kind={selectedCase.kind} id={selectedCase.id} perspective="applicant" onChanged={refreshPage} /></section>}
    {!loading && !error && <ApplicantRequestsList residentRequests={residentRequests} companyRequests={companyRequests} houseRequests={houseRequests} pageItems={requests} totalCount={total} selectedCase={selectedCase} onChanged={refreshPage} onOpen={(request) => setSelectedCase((current) => current?.kind === request.kind && current.id === request.id ? null : request)} fullPage />}
    {total > PAGE_SIZE && <div className="admin-pagination"><button type="button" className="button button--soft" disabled={loading || offset === 0} onClick={() => { setOffset(Math.max(0, offset - PAGE_SIZE)); setSelectedCase(null); }}>Назад</button><span>Страница {Math.floor(offset / PAGE_SIZE) + 1} из {Math.ceil(total / PAGE_SIZE)}</span><button type="button" className="button button--soft" disabled={loading || offset + PAGE_SIZE >= total} onClick={() => { setOffset(offset + PAGE_SIZE); setSelectedCase(null); }}>Далее</button></div>}
  </div>;
}
