import { useEffect, useState } from "react";

import { accessRequestStatusLabels, listAccessRequestsPage, requestAccessCancellation, type AccessRequestDetail, type AccessRequestKind, type AccessRequestStatus, type AccessRequestsPage } from "../features/issues/integrations/access_actions_api";
import { isDemoMode } from "../features/issues/integrations/client_api";
import { formatDate, type CompanyRegistrationRequest, type HouseAdditionRequest, type ResidentRequest } from "../features/issues/types";
import { NotificationToggle } from "../features/notifications/ui/NotificationToggle";
import { HttpError } from "../shared/base_http_client";
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

const PAGE_SIZE = 20;

function summaries({ residentRequests, companyRequests, houseRequests }: RequestLists): RequestSummary[] {
  return [
    ...residentRequests.map((item) => ({ kind: "resident" as const, id: item.id, title: item.address ?? "Дом по заявке", description: `Доступ к дому · подъезд №${item.entrance}, кв. ${item.apartment}`, status: item.status, outcome: item.outcome, decisionNote: item.decisionNote, createdAt: item.createdAt })),
    ...companyRequests.map((item) => ({ kind: "company_registration" as const, id: item.id, title: item.companyName || "Регистрация УК", description: "Регистрация управляющей компании", status: item.status, outcome: item.outcome, decisionNote: item.decisionNote, createdAt: item.createdAt })),
    ...houseRequests.map((item) => ({ kind: "house_addition" as const, id: item.id, title: item.address, description: "Подключение дома", status: item.status, outcome: item.outcome, decisionNote: item.decisionNote, createdAt: item.createdAt })),
  ].sort((a, b) => b.createdAt.localeCompare(a.createdAt));
}

function summaryFromPage(item: AccessRequestDetail): RequestSummary {
  const common = { kind: item.kind, id: item.id, status: item.status, outcome: item.outcome ?? undefined, decisionNote: item.decision_note ?? undefined, createdAt: item.created_at };
  if (item.kind === "resident") return {
    ...common,
    title: item.address_display ?? "Дом по заявке",
    description: `Доступ к дому · подъезд №${item.submitted_entrance_number ?? "—"}, кв. ${item.submitted_apartment_number ?? "—"}`,
  };
  if (item.kind === "company_registration") return {
    ...common,
    title: item.proposed_company_name || "Регистрация УК",
    description: "Регистрация управляющей компании",
  };
  return {
    ...common,
    title: item.entered_address || "Подключение дома",
    description: "Подключение дома",
  };
}

function statusLabel(request: RequestSummary): string {
  if (request.status === "closed" && request.outcome === "granted") return "Доступ выдан";
  if (request.status === "closed" && request.outcome === "approved") return "Одобрена";
  if (request.status === "closed" && (request.outcome === "denied" || request.outcome === "rejected")) return "Отказано";
  return accessRequestStatusLabels[request.status as AccessRequestStatus] ?? request.status;
}

export function ApplicantRequestsList({ residentRequests, companyRequests, houseRequests, onOpen, limit, selectedCase, onChanged, pageItems, totalCount }: RequestLists & { onOpen: (item: ApplicantCase) => void; limit?: number; selectedCase?: ApplicantCase | null; onChanged?: () => Promise<void>; pageItems?: RequestSummary[]; totalCount?: number }) {
  const [cancelBusyId, setCancelBusyId] = useState<string | null>(null);
  const [cancelError, setCancelError] = useState<{ id: string; message: string } | null>(null);
  const requests = pageItems ?? summaries({ residentRequests, companyRequests, houseRequests });
  if (requests.length === 0) return null;

  async function cancelOpenResidentRequest(id: string) {
    if (!onChanged || !window.confirm("Отменить заявку на доступ к дому?")) return;
    setCancelBusyId(id); setCancelError(null);
    try { await requestAccessCancellation("resident", id); await onChanged(); }
    catch (reason) { setCancelError({ id, message: reason instanceof Error ? reason.message : "Не удалось отменить заявку" }); }
    finally { setCancelBusyId(null); }
  }

  return <section className="requests-list">
    <div className="section-heading"><h2>Мои заявки</h2><span className="count-badge">{totalCount ?? requests.length}</span></div>
    {requests.slice(0, limit).map((request) => {
      const expanded = selectedCase?.kind === request.kind && selectedCase.id === request.id;
      return <article className="panel request-card" key={`${request.kind}:${request.id}`}>
        <strong>{request.title}</strong>
        <span>{request.description}</span>
        <small>{formatDate(request.createdAt)} · {statusLabel(request)}</small>
        {request.decisionNote && <span>Пояснение: {request.decisionNote}</span>}
        {request.kind === "resident" && <NotificationToggle subject="resident_request" id={request.id} />}
        <div className="button-row"><button type="button" className="button button--soft" aria-expanded={onChanged ? expanded : undefined} onClick={() => onOpen({ kind: request.kind, id: request.id })}>{expanded ? "Скрыть обсуждение" : isDemoMode ? "Открыть заявку" : "Открыть заявку и обсуждение"}</button>{!isDemoMode && onChanged && request.kind === "resident" && request.status === "open" && !expanded && <button type="button" className="button button--soft" disabled={cancelBusyId !== null} onClick={() => void cancelOpenResidentRequest(request.id)}>Отменить заявку</button>}</div>
        {cancelError?.id === request.id && <p className="form-error" role="alert">{cancelError.message}</p>}
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

  return <div className="page page--form">
    <ScreenHeader title="Мои заявки" subtitle="Статус и переписка" onBack={onBack} />
    {loading && <p className="muted-text">Загружаем заявки…</p>}
    {error && <p className="form-error" role="alert">{error}</p>}
    {!loading && !error && total === 0 && !selectedCase && <div className="panel empty-state"><strong>Заявок пока нет</strong><p>Поданные заявки появятся здесь.</p></div>}
    {selectedCase && !selectedIsListed && <section className="panel request-card"><strong>Поданная заявка</strong><p className="muted-text">Она не находится на текущей странице списка.</p><AccessCasePanel key={`${selectedCase.kind}:${selectedCase.id}`} kind={selectedCase.kind} id={selectedCase.id} perspective="applicant" onChanged={refreshPage} /></section>}
    {!loading && !error && <ApplicantRequestsList residentRequests={residentRequests} companyRequests={companyRequests} houseRequests={houseRequests} pageItems={requests} totalCount={total} selectedCase={selectedCase} onChanged={refreshPage} onOpen={(request) => setSelectedCase((current) => current?.kind === request.kind && current.id === request.id ? null : request)} />}
    {total > PAGE_SIZE && <div className="admin-pagination"><button type="button" className="button button--soft" disabled={loading || offset === 0} onClick={() => { setOffset(Math.max(0, offset - PAGE_SIZE)); setSelectedCase(null); }}>Назад</button><span>Страница {Math.floor(offset / PAGE_SIZE) + 1} из {Math.ceil(total / PAGE_SIZE)}</span><button type="button" className="button button--soft" disabled={loading || offset + PAGE_SIZE >= total} onClick={() => { setOffset(offset + PAGE_SIZE); setSelectedCase(null); }}>Далее</button></div>}
  </div>;
}
