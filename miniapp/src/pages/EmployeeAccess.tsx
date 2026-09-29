import { useEffect, useState } from "react";

import { getCurrentUser } from "../features/auth/integrations/client_api";
import {
  accessRequestStatusLabels,
  addAccessDiscussionMessage,
  changeAccessRequestStatus,
  changeResidentGrant,
  getAccessRequest,
  listAccessRequestsPage,
  requestAccessCancellation,
  resolveAccessCancellation,
  revokeStaff,
  updateResidentAccessRequest,
  type AccessRequestDetail,
  type AccessRequestKind,
  type AccessRequestsPage,
  type AccessRequestStatus,
  type AccessDiscussionMessage,
} from "../features/issues/integrations/access_actions_api";
import { isDemoMode, issuesClient } from "../features/issues/integrations/client_api";
import { demoResident, formatDate, type House, type HouseAdditionRequest, type ResidentGrant, type ResidentOffer, type ResidentRequest, type StaffAssignment, type StaffRights } from "../features/issues/types";
import { Icon } from "../shared/common_ui/Icon";
import { ScreenHeader } from "../shared/common_ui/ScreenHeader";
import { HttpError } from "../shared/base_http_client";

type Tab = "residents" | "staff" | "houses";
const noRights: StaffRights = { manageStaff: false, manageResidents: false, manageIssues: false };
const PAGE_SIZE = 20;

function residentRequestFromPage(item: AccessRequestDetail): ResidentRequest {
  return {
    id: item.id, houseId: item.house_id ?? "", address: item.address_display,
    fullName: item.submitted_full_name ?? "", entrance: item.submitted_entrance_number ?? 0,
    apartment: item.submitted_apartment_number ?? 0, status: item.status,
    outcome: item.outcome === "granted" || item.outcome === "denied" ? item.outcome : undefined,
    decisionNote: item.decision_note ?? undefined, createdAt: item.created_at,
  };
}

function houseRequestFromPage(item: AccessRequestDetail): HouseAdditionRequest {
  return {
    id: item.id, companyId: item.company_id ?? undefined,
    registrationRequestId: item.registration_request_id ?? undefined,
    address: item.entered_address ?? "", explanation: item.free_text ?? undefined,
    status: item.status,
    outcome: item.outcome === "approved" || item.outcome === "rejected" ? item.outcome : undefined,
    decisionNote: item.decision_note ?? undefined, createdAt: item.created_at,
  };
}

function RequestPager({ total, offset, loading, onPage }: { total: number; offset: number; loading: boolean; onPage: (offset: number) => void }) {
  if (total <= PAGE_SIZE) return null;
  return <div className="admin-pagination"><button type="button" className="button button--soft" disabled={loading || offset === 0} onClick={() => onPage(Math.max(0, offset - PAGE_SIZE))}>Назад</button><span>Страница {Math.floor(offset / PAGE_SIZE) + 1} из {Math.ceil(total / PAGE_SIZE)}</span><button type="button" className="button button--soft" disabled={loading || offset + PAGE_SIZE >= total} onClick={() => onPage(offset + PAGE_SIZE)}>Далее</button></div>;
}

function discussionAuthor(message: AccessDiscussionMessage, actorId: string, applicantId: string | null | undefined, perspective: "applicant" | "staff"): string {
  if (message.author_user_id === actorId) return "Вы";
  if (message.author_kind === "admin") return "Администратор";
  if (message.author_kind === "support") return "Поддержка";
  if (message.author_kind === "employee") return "Сотрудник УК";
  if (message.author_user_id === applicantId) return perspective === "staff" ? "Житель" : "Заявитель";
  return "Другая сторона";
}

interface AccessCasePanelProps {
  kind: AccessRequestKind;
  id: string;
  perspective: "applicant" | "staff";
  canManage?: boolean;
  onChanged: () => Promise<void>;
}

export function AccessCasePanel({ kind, id, perspective, canManage = false, onChanged }: AccessCasePanelProps) {
  const [detail, setDetail] = useState<AccessRequestDetail | null>(null);
  const [actorId, setActorId] = useState("");
  const [loading, setLoading] = useState(!isDemoMode);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [message, setMessage] = useState("");
  const [statusChoice, setStatusChoice] = useState<"open" | "reviewing" | "needs_info">("open");
  const [editing, setEditing] = useState(false);
  const [fullName, setFullName] = useState("");
  const [entrance, setEntrance] = useState("");
  const [apartment, setApartment] = useState("");

  useEffect(() => {
    if (isDemoMode) return;
    let cancelled = false;
    setLoading(true); setError("");
    void Promise.all([getAccessRequest(kind, id), getCurrentUser()]).then(([request, user]) => {
      if (cancelled) return;
      setDetail(request);
      setActorId(user.id);
      setStatusChoice(request.status === "closed" || request.status === "cancelled" ? "open" : request.status);
      setFullName(request.submitted_full_name ?? "");
      setEntrance(String(request.submitted_entrance_number ?? ""));
      setApartment(String(request.submitted_apartment_number ?? ""));
    }).catch((reason) => { if (!cancelled) setError(reason instanceof Error ? reason.message : "Не удалось открыть заявку"); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [kind, id]);

  async function run(action: () => Promise<unknown>, success: string, after?: () => void) {
    setBusy(true); setError(""); setNotice("");
    try {
      await action();
      after?.();
      setNotice(success);
      try {
        const request = await getAccessRequest(kind, id);
        setDetail(request);
        setStatusChoice(request.status === "closed" || request.status === "cancelled" ? "open" : request.status);
        await onChanged();
      } catch (reason) {
        setError(`Действие сохранено, но данные не обновились: ${reason instanceof Error ? reason.message : "попробуйте открыть заявку заново"}`);
      }
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Действие не выполнено"); }
    finally { setBusy(false); }
  }

  if (isDemoMode) return <div className="decision-box"><p className="muted-text">Полное обсуждение и дополнительные действия доступны при подключении к серверу.</p></div>;
  if (loading) return <div className="decision-box">Загружаем заявку…</div>;
  if (!detail) return <div className="decision-box"><p className="form-error" role="alert">{error || "Заявка не найдена"}</p></div>;

  const active = detail.status !== "closed" && detail.status !== "cancelled";
  const canWrite = perspective === "applicant" || canManage;
  const requesterIsApplicant = Boolean(detail.applicant_user_id && detail.cancel_requested_by === detail.applicant_user_id);
  const canResolveCancellation = active && detail.cancel_requested_by && actorId && (
    perspective === "applicant"
      ? requesterIsApplicant === false && Boolean(detail.applicant_user_id)
      : canManage && requesterIsApplicant
  );
  const canRequestCancellation = active && canWrite && !detail.cancel_requested_by;

  return <div className="decision-box">
    <h3>Заявка и обсуждение</h3>
    <p className="section-description">{accessRequestStatusLabels[detail.status]}{detail.outcome && ` · ${detail.outcome === "granted" || detail.outcome === "approved" ? "одобрено" : "отказано"}`}</p>
    {detail.decision_note && <p>Пояснение к решению: {detail.decision_note}</p>}
    {detail.cancel_requested_by && <div className="info-panel"><Icon name="info" size={20} /> {canResolveCancellation ? "Другая сторона просит отменить заявку. Подтвердите отмену или оставьте заявку в работе." : detail.cancel_requested_by === actorId ? "Вы запросили отмену. Ждём ответа другой стороны." : "Запрошена отмена заявки."}</div>}
    {canResolveCancellation && <div className="button-row"><button type="button" className="button button--soft" disabled={busy} onClick={() => void run(() => resolveAccessCancellation(kind, id, false), "Отмена отклонена")}>Оставить заявку</button><button type="button" className="button button--primary" disabled={busy} onClick={() => void run(() => resolveAccessCancellation(kind, id, true), "Заявка отменена")}>Подтвердить отмену</button></div>}
    {canRequestCancellation && <button type="button" className="button button--soft" disabled={busy} onClick={() => void run(() => requestAccessCancellation(kind, id), detail.status === "open" && kind === "resident" && perspective === "applicant" ? "Заявка отменена" : "Запрос на отмену отправлен")}>{detail.status === "open" && kind === "resident" && perspective === "applicant" ? "Отменить заявку" : "Запросить отмену"}</button>}

    {kind === "resident" && perspective === "staff" && canManage && active && <div className="form-stack"><label className="field"><span>Рабочий статус</span><select value={statusChoice} onChange={(event) => setStatusChoice(event.target.value as "open" | "reviewing" | "needs_info")}><option value="open">Открыта</option><option value="reviewing">На рассмотрении</option><option value="needs_info">Нужны уточнения</option></select></label><button type="button" className="button button--soft" disabled={busy || statusChoice === detail.status} onClick={() => void run(() => changeAccessRequestStatus(kind, id, statusChoice), "Статус сохранён")}>Сохранить статус</button></div>}

    {kind === "resident" && perspective === "applicant" && active && <div>{editing ? <form className="form-stack" onSubmit={(event) => { event.preventDefault(); void run(() => updateResidentAccessRequest(id, fullName, Number(entrance), Number(apartment)), "Данные заявки исправлены", () => setEditing(false)); }}><label className="field"><span>ФИО</span><input required value={fullName} onChange={(event) => setFullName(event.target.value)} /></label><div className="field-grid"><label className="field"><span>Подъезд</span><input required type="number" min="1" value={entrance} onChange={(event) => setEntrance(event.target.value)} /></label><label className="field"><span>Квартира</span><input required type="number" min="1" value={apartment} onChange={(event) => setApartment(event.target.value)} /></label></div><div className="button-row"><button type="button" className="button button--soft" onClick={() => setEditing(false)}>Не менять</button><button type="submit" className="button button--primary" disabled={busy || !fullName.trim() || Number(entrance) < 1 || Number(apartment) < 1}>Сохранить</button></div></form> : <button type="button" className="button button--soft" disabled={busy} onClick={() => setEditing(true)}>Исправить ФИО или квартиру</button>}</div>}

    <div className="section-heading"><h3>Обсуждение</h3><span className="count-badge">{detail.discussion.length}</span></div>
    <div className="message-list">{detail.discussion.length ? detail.discussion.map((item) => <article className="message" key={item.id}><span className="message__avatar"><Icon name={item.author_user_id === actorId ? "user" : "chat"} size={19} /></span><div><div className="message__heading"><strong>{discussionAuthor(item, actorId, detail.applicant_user_id, perspective)}</strong><time>{formatDate(item.created_at)}</time></div><p>{item.text}</p></div></article>) : <p className="muted-text">Сообщений пока нет.</p>}</div>
    {canWrite && detail.status !== "cancelled" && <form className="comment-form" onSubmit={(event) => { event.preventDefault(); if (message.trim()) void run(() => addAccessDiscussionMessage(kind, id, message), "Сообщение отправлено", () => setMessage("")); }}><input aria-label="Сообщение в обсуждении заявки" value={message} onChange={(event) => setMessage(event.target.value)} placeholder="Написать сообщение…" /><button type="submit" className="icon-button icon-button--blue" aria-label="Отправить сообщение" disabled={busy || !message.trim()}><Icon name="send" size={19} /></button></form>}
    {notice && <p className="form-success" role="status">{notice}</p>}
    {error && <p className="form-error" role="alert">{error}</p>}
  </div>;
}

interface Props {
  houses: House[];
  requests: ResidentRequest[];
  grants: ResidentGrant[];
  offers: ResidentOffer[];
  staff: StaffAssignment[];
  houseRequests: HouseAdditionRequest[];
  permissions: StaffRights;
  companyId: string;
  companies: { id: string; name: string }[];
  onCompanyChange: (id: string) => void;
  onBack: () => void;
  onChanged: () => Promise<void>;
}

export function EmployeeAccess({ houses, requests, grants, offers, staff, houseRequests, permissions, companyId, companies, onCompanyChange, onBack, onChanged }: Props) {
  const [tab, setTab] = useState<Tab>("residents");
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [busy, setBusy] = useState(false);
  const [offerHouseId, setOfferHouseId] = useState(houses[0]?.id ?? "");
  const [offerPhone, setOfferPhone] = useState(isDemoMode ? demoResident.phone : "");
  const [offerEntrance, setOfferEntrance] = useState("");
  const [offerApartment, setOfferApartment] = useState("");
  const [staffPhone, setStaffPhone] = useState("");
  const [rights, setRights] = useState<StaffRights>(noRights);
  const [selectedStaffId, setSelectedStaffId] = useState("");
  const [confirmStaffRevoke, setConfirmStaffRevoke] = useState(false);
  const [newHouseAddress, setNewHouseAddress] = useState("");
  const [newHouseNote, setNewHouseNote] = useState("");
  const [decisionId, setDecisionId] = useState("");
  const [decisionOutcome, setDecisionOutcome] = useState<"granted" | "denied">("granted");
  const [decisionNote, setDecisionNote] = useState("");
  const [selectedCase, setSelectedCase] = useState<{ kind: AccessRequestKind; id: string } | null>(null);
  const [residentOffset, setResidentOffset] = useState(0);
  const [residentPage, setResidentPage] = useState<AccessRequestsPage | null>(null);
  const [residentLoading, setResidentLoading] = useState(!isDemoMode);
  const [residentError, setResidentError] = useState("");
  const [houseOffset, setHouseOffset] = useState(0);
  const [housePage, setHousePage] = useState<AccessRequestsPage | null>(null);
  const [houseLoading, setHouseLoading] = useState(!isDemoMode);
  const [houseError, setHouseError] = useState("");
  const [companyOffset, setCompanyOffset] = useState(0);
  const [companyPage, setCompanyPage] = useState<AccessRequestsPage | null>(null);
  const [companyLoading, setCompanyLoading] = useState(!isDemoMode);
  const [companyRequestsError, setCompanyRequestsError] = useState("");
  const [requestsRevision, setRequestsRevision] = useState(0);
  const [selectedGrantId, setSelectedGrantId] = useState("");
  const [grantAction, setGrantAction] = useState<"extend" | "revoke">("extend");
  const [grantValidTo, setGrantValidTo] = useState("");
  const [grantReason, setGrantReason] = useState("");
  const [confirmRevoke, setConfirmRevoke] = useState(false);

  useEffect(() => { setOfferHouseId(houses[0]?.id ?? ""); }, [companyId, houses]);
  useEffect(() => { setSelectedStaffId(""); setConfirmStaffRevoke(false); setSelectedCase(null); setResidentOffset(0); setHouseOffset(0); }, [companyId]);
  useEffect(() => {
    if (isDemoMode) return;
    let cancelled = false;
    setCompanyLoading(true); setCompanyRequestsError(""); setCompanyPage(null);
    void listAccessRequestsPage("company_registration", companyOffset, PAGE_SIZE)
      .then((result) => { if (!cancelled) setCompanyPage(result); })
      .catch((reason) => { if (!cancelled) setCompanyRequestsError(reason instanceof HttpError && reason.status === 403 ? "Нет доступа к обращениям УК" : reason instanceof Error ? reason.message : "Не удалось загрузить обращения УК"); })
      .finally(() => { if (!cancelled) setCompanyLoading(false); });
    return () => { cancelled = true; };
  }, [companyOffset, requestsRevision]);
  useEffect(() => {
    if (isDemoMode) return;
    if (!companyId) { setResidentPage(null); setResidentError("Нет доступа к управляющей компании"); setResidentLoading(false); return; }
    let cancelled = false;
    setResidentLoading(true); setResidentError(""); setResidentPage(null);
    void listAccessRequestsPage("resident", residentOffset, PAGE_SIZE, undefined, false, companyId)
      .then((result) => { if (!cancelled) setResidentPage(result); })
      .catch((reason) => { if (!cancelled) setResidentError(reason instanceof HttpError && reason.status === 403 ? "Нет доступа к заявкам этой УК" : reason instanceof Error ? reason.message : "Не удалось загрузить заявки жильцов"); })
      .finally(() => { if (!cancelled) setResidentLoading(false); });
    return () => { cancelled = true; };
  }, [companyId, residentOffset, requestsRevision]);
  useEffect(() => {
    if (isDemoMode) return;
    if (!companyId) { setHousePage(null); setHouseError("Нет доступа к управляющей компании"); setHouseLoading(false); return; }
    let cancelled = false;
    setHouseLoading(true); setHouseError(""); setHousePage(null);
    void listAccessRequestsPage("house_addition", houseOffset, PAGE_SIZE, undefined, false, companyId)
      .then((result) => { if (!cancelled) setHousePage(result); })
      .catch((reason) => { if (!cancelled) setHouseError(reason instanceof HttpError && reason.status === 403 ? "Нет доступа к заявкам на дома" : reason instanceof Error ? reason.message : "Не удалось загрузить заявки на дома"); })
      .finally(() => { if (!cancelled) setHouseLoading(false); });
    return () => { cancelled = true; };
  }, [companyId, houseOffset, requestsRevision]);

  const visibleRequests = isDemoMode ? requests.slice(residentOffset, residentOffset + PAGE_SIZE) : residentPage?.items.map(residentRequestFromPage) ?? [];
  const residentTotal = isDemoMode ? requests.length : residentPage?.total ?? 0;
  const visibleHouseRequests = isDemoMode ? houseRequests.slice(houseOffset, houseOffset + PAGE_SIZE) : housePage?.items.map(houseRequestFromPage) ?? [];
  const houseTotal = isDemoMode ? houseRequests.length : housePage?.total ?? 0;
  const companyRequests = companyPage?.items ?? [];
  const companyTotal = companyPage?.total ?? 0;

  async function refreshRequests() {
    await onChanged();
    setRequestsRevision((current) => current + 1);
  }

  async function run(action: () => Promise<unknown>, after: () => void, message: string) {
    setBusy(true); setError(""); setSuccess("");
    try { await action(); await refreshRequests(); after(); setSuccess(message); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Не удалось сохранить изменение"); }
    finally { setBusy(false); }
  }

  function houseName(id: string): string { return houses.find((item) => item.id === id)?.address ?? "Дом"; }
  function toggleRight(key: keyof StaffRights) { setRights((current) => ({ ...current, [key]: !current[key] })); }
  function toggleCase(kind: AccessRequestKind, id: string) {
    setSelectedCase((current) => current?.kind === kind && current.id === id ? null : { kind, id });
  }
  function openGrant(grant: ResidentGrant) {
    setSelectedGrantId((current) => current === grant.id ? "" : grant.id);
    setGrantAction(grant.validUntil ? "extend" : "revoke");
    setGrantValidTo(""); setGrantReason(""); setConfirmRevoke(false);
  }
  async function refreshCompanyRequests() {
    await refreshRequests();
  }
  async function submitStaffRevoke() {
    const assignment = staff.find((item) => item.id === selectedStaffId);
    if (!permissions.manageStaff || !assignment || !confirmStaffRevoke || busy || isDemoMode) return;
    await run(() => revokeStaff(assignment.id), () => {
      setSelectedStaffId("");
      setConfirmStaffRevoke(false);
    }, "Назначение сотрудника отозвано");
  }
  const selectedGrant = grants.find((item) => item.id === selectedGrantId);
  const selectedStaff = staff.find((item) => item.id === selectedStaffId);
  const newExpiry = grantValidTo ? new Date(grantValidTo) : null;
  const canExtend = grantAction === "extend" && selectedGrant?.validUntil && newExpiry
    && !Number.isNaN(newExpiry.getTime()) && newExpiry.getTime() > Date.now()
    && newExpiry.getTime() > new Date(selectedGrant.validUntil).getTime();

  return (
    <div className="page page--employee-access">
      <ScreenHeader title="Доступы и дома" subtitle="Кабинет УК" onBack={onBack} />
      {companies.length > 1 && <label className="field company-picker"><span>Управляющая компания</span><select value={companyId} onChange={(event) => onCompanyChange(event.target.value)}>{companies.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>}
      {isDemoMode && <div className="info-panel"><Icon name="info" size={20} /> Изменения сохраняются только в этом браузере.</div>}
      <div className="segmented segmented--three" role="tablist" aria-label="Раздел кабинета УК">
        <button type="button" role="tab" aria-selected={tab === "residents"} className={tab === "residents" ? "is-active" : ""} onClick={() => setTab("residents")}>Жильцы</button>
        <button type="button" role="tab" aria-selected={tab === "staff"} className={tab === "staff" ? "is-active" : ""} onClick={() => setTab("staff")}>Сотрудники</button>
        <button type="button" role="tab" aria-selected={tab === "houses"} className={tab === "houses" ? "is-active" : ""} onClick={() => setTab("houses")}>Дома</button>
      </div>
      {error && <p className="form-error" role="alert">{error}</p>}
      {success && <p className="form-success" role="status"><Icon name="check" size={17} /> {success}</p>}

      {tab === "residents" && <div className="management-stack">
        <section className="panel form-panel">
          <div className="section-heading"><h2>Заявки на доступ</h2><span className="count-badge">{residentTotal}</span></div>
          {!permissions.manageResidents && <p className="field-help">У вас нет права рассматривать заявки жильцов и выдавать им доступ. Просмотр заявок доступен.</p>}
          {residentLoading && <p className="muted-text">Загружаем заявки…</p>}
          {residentError && <p className="form-error" role="alert">{residentError}</p>}
          {!residentLoading && !residentError && residentTotal === 0 && permissions.manageResidents && <p className="muted-text">Заявок пока нет.</p>}
          <div className="management-list">{visibleRequests.map((request) => <article className="management-item" key={request.id}>
            <div><strong>{request.fullName}</strong><p>{houseName(request.houseId)} · подъезд №{request.entrance}, кв. {request.apartment}</p><small>{formatDate(request.createdAt)} · {request.status === "closed" ? request.outcome === "granted" ? "Доступ выдан" : "Отказано" : accessRequestStatusLabels[request.status]}</small>{request.decisionNote && <p>Пояснение: {request.decisionNote}</p>}</div>
            <div className="button-row"><button type="button" className="button button--soft" aria-expanded={selectedCase?.kind === "resident" && selectedCase.id === request.id} onClick={() => toggleCase("resident", request.id)}>Обсуждение</button>{permissions.manageResidents && request.status !== "closed" && request.status !== "cancelled" && <button type="button" className="button button--soft" onClick={() => { setDecisionId(request.id); setDecisionNote(""); }}>Решение</button>}</div>
          </article>)}</div>
          {selectedCase?.kind === "resident" && visibleRequests.some((item) => item.id === selectedCase.id) && <AccessCasePanel key={selectedCase.id} kind="resident" id={selectedCase.id} perspective="staff" canManage={permissions.manageResidents} onChanged={refreshRequests} />}
          <RequestPager total={residentTotal} offset={residentOffset} loading={residentLoading} onPage={(next) => { setResidentOffset(next); setSelectedCase(null); setDecisionId(""); }} />
          {permissions.manageResidents && decisionId && <div className="decision-box"><h3>Решение по заявке</h3><label className="field"><span>Результат</span><select value={decisionOutcome} onChange={(event) => setDecisionOutcome(event.target.value as "granted" | "denied")}><option value="granted">Выдать доступ</option><option value="denied">Отказать</option></select></label><label className="field"><span>Пояснение · обязательно</span><textarea rows={2} value={decisionNote} onChange={(event) => setDecisionNote(event.target.value)} placeholder="Причина решения" /></label><div className="button-row"><button className="button button--soft" type="button" onClick={() => setDecisionId("")}>Отмена</button><button className="button button--primary" type="button" disabled={busy || !decisionNote.trim()} onClick={() => void run(() => issuesClient.decideResidentRequest(decisionId, decisionOutcome, decisionNote), () => { setDecisionId(""); setDecisionNote(""); setSelectedCase(null); }, isDemoMode ? "Пробное решение сохранено" : "Решение отправлено")}>Сохранить решение</button></div></div>}
        </section>

        <section className="panel form-panel"><div className="section-heading"><h2>Доступы жильцов</h2><span className="count-badge">{grants.length}</span></div>{grants.length === 0 && <p className="muted-text">Выданных доступов пока нет.</p>}<div className="management-list">{grants.map((grant) => <article className="management-item" key={grant.id}><span className="small-icon"><Icon name="home" /></span><div><strong>{grant.fullName}</strong><p>{houseName(grant.houseId)} · подъезд №{grant.entrance}, кв. {grant.apartment}</p><small>{grant.phone} · {grant.status === "revoked" ? "отозван" : grant.status === "expired" ? "истёк" : "действует"}{grant.validUntil && ` · до ${formatDate(grant.validUntil)}`}</small></div>{permissions.manageResidents && grant.status !== "revoked" && <button type="button" className="button button--soft" aria-expanded={selectedGrantId === grant.id} onClick={() => openGrant(grant)}>Доступ</button>}</article>)}</div>
          {permissions.manageResidents && selectedGrant && selectedGrant.status !== "revoked" && <div className="decision-box"><h3>Изменить доступ</h3><p>{selectedGrant.fullName} · {houseName(selectedGrant.houseId)}, кв. {selectedGrant.apartment}</p><label className="field"><span>Действие</span><select value={grantAction} onChange={(event) => { setGrantAction(event.target.value as "extend" | "revoke"); setConfirmRevoke(false); }}><option value="extend" disabled={!selectedGrant.validUntil}>Продлить срок</option><option value="revoke">Отозвать доступ</option></select></label>{grantAction === "extend" ? <><p className="field-help">Текущий срок: {selectedGrant.validUntil ? formatDate(selectedGrant.validUntil) : "без ограничения"}. Новый срок должен быть позднее.</p><label className="field"><span>Новый срок</span><input required type="datetime-local" value={grantValidTo} onChange={(event) => setGrantValidTo(event.target.value)} /></label></> : <><label className="field"><span>Причина отзыва</span><textarea rows={2} value={grantReason} onChange={(event) => setGrantReason(event.target.value)} placeholder="Почему доступ больше не должен действовать" /></label><label className="checkbox-row"><input type="checkbox" checked={confirmRevoke} onChange={(event) => setConfirmRevoke(event.target.checked)} /><span>Подтверждаю отзыв доступа к дому</span></label></>}<div className="button-row"><button type="button" className="button button--soft" onClick={() => setSelectedGrantId("")}>Закрыть</button><button type="button" className="button button--primary" disabled={busy || (grantAction === "extend" ? !canExtend : !grantReason.trim() || !confirmRevoke)} onClick={() => void run(() => changeResidentGrant(selectedGrant.id, grantAction, grantAction === "extend" ? newExpiry?.toISOString() : undefined, grantAction === "revoke" ? grantReason : undefined), () => { setSelectedGrantId(""); setGrantValidTo(""); setGrantReason(""); setConfirmRevoke(false); }, grantAction === "extend" ? "Доступ продлён" : "Доступ отозван")}>Сохранить</button></div></div>}
        </section>

        {permissions.manageResidents && <section className="panel form-panel"><h2>Предложить доступ по номеру</h2><p className="section-description">Доступ появится только после принятия человеком с подтверждённого номера.{isDemoMode && ` Номер деможителя: ${demoResident.phone}.`}</p><form onSubmit={(event) => { event.preventDefault(); void run(() => issuesClient.createResidentOffer({ houseId: offerHouseId, phone: offerPhone, entrance: Number(offerEntrance), apartment: Number(offerApartment) }), () => { setOfferPhone(""); setOfferEntrance(""); setOfferApartment(""); }, isDemoMode ? "Предложение сохранено в демо" : "Предложение отправлено"); }}><label className="field"><span>Дом</span><select value={offerHouseId} onChange={(event) => setOfferHouseId(event.target.value)}>{houses.map((house) => <option key={house.id} value={house.id}>{house.address}</option>)}</select></label><label className="field"><span>Номер телефона</span><input required type="tel" value={offerPhone} onChange={(event) => setOfferPhone(event.target.value)} placeholder="+7 999 123-45-67" /></label><div className="field-grid"><label className="field"><span>Подъезд</span><input required type="number" min="1" value={offerEntrance} onChange={(event) => setOfferEntrance(event.target.value)} /></label><label className="field"><span>Квартира</span><input required type="number" min="1" value={offerApartment} onChange={(event) => setOfferApartment(event.target.value)} /></label></div><button type="submit" className="button button--primary button--wide management-submit" disabled={busy || !offerHouseId}>{isDemoMode ? "Создать пробное предложение" : "Отправить предложение"}</button></form>{offers.length > 0 && <div className="management-list"><h3>Предложения</h3>{offers.map((offer) => <article className="management-item" key={offer.id}><div><strong>{offer.phone}</strong><p>{houseName(offer.houseId)} · подъезд №{offer.entrance}, кв. {offer.apartment}</p><small>{offer.status === "pending" ? "Ожидает ответа" : offer.status === "accepted" ? "Принято" : offer.status === "cancelled" ? "Отменено" : "Отклонено"}</small></div></article>)}</div>}</section>}
      </div>}

      {tab === "staff" && <div className="management-stack">
        <section className="panel form-panel">
          <div className="section-heading"><h2>Сотрудники</h2><span className="count-badge">{staff.length}</span></div>
          {!permissions.manageStaff && <p className="field-help">У вас нет права добавлять сотрудников, менять их права и отзывать назначения. Просмотр списка доступен.</p>}
          {staff.length === 0 && permissions.manageStaff && <p className="muted-text">Назначений пока нет.</p>}
          <div className="management-list">{staff.map((person) => <article className="management-item" key={person.id}>
            <span className="small-icon"><Icon name="user" /></span>
            <div><strong>{person.fullName ?? person.phone}</strong><p>{person.phone}{!person.bound && " · ожидает входа"}</p><div className="rights-tags">{person.rights.manageStaff && <span>Сотрудники</span>}{person.rights.manageResidents && <span>Жильцы</span>}{person.rights.manageIssues && <span>Проблемы</span>}</div></div>
            {permissions.manageStaff && <span className="staff-management-actions">
              <button type="button" className="button button--soft" onClick={() => { setStaffPhone(person.phone); setRights({ ...person.rights }); }}>Права</button>
              {!isDemoMode && <button type="button" className="button button--soft" aria-expanded={selectedStaffId === person.id} onClick={() => { setSelectedStaffId(person.id); setConfirmStaffRevoke(false); }}>Отозвать</button>}
            </span>}
          </article>)}</div>
          {permissions.manageStaff && !isDemoMode && selectedStaff && <div className="decision-box">
            <h3>Отозвать назначение сотрудника?</h3>
            <p>{selectedStaff.fullName ?? selectedStaff.phone} · {selectedStaff.phone}. После отзыва сотрудник потеряет права в кабинете этой УК.</p>
            <label className="checkbox-row"><input type="checkbox" checked={confirmStaffRevoke} onChange={(event) => setConfirmStaffRevoke(event.target.checked)} /><span>Подтверждаю отзыв назначения</span></label>
            <div className="button-row">
              <button type="button" className="button button--soft" onClick={() => { setSelectedStaffId(""); setConfirmStaffRevoke(false); }}>Отмена</button>
              <button type="button" className="button button--primary" disabled={busy || !confirmStaffRevoke} onClick={() => void submitStaffRevoke()}>Отозвать права</button>
            </div>
          </div>}
        </section>
        {permissions.manageStaff && <section className="panel form-panel"><h2>Добавить или изменить сотрудника</h2><p className="section-description">Назначение привяжется к человеку после входа с подтверждённым номером.</p><form onSubmit={(event) => { event.preventDefault(); void run(() => issuesClient.assignStaff(companyId, staffPhone, rights), () => { setStaffPhone(""); setRights(noRights); }, isDemoMode ? "Пробное назначение сохранено" : "Права сотрудника сохранены"); }}><label className="field"><span>Номер телефона</span><input required type="tel" value={staffPhone} onChange={(event) => setStaffPhone(event.target.value)} placeholder="+7 999 123-45-67" /></label><div className="rights-list"><label className="checkbox-row"><input type="checkbox" checked={rights.manageStaff} onChange={() => toggleRight("manageStaff")} /><span>Управлять сотрудниками и правами</span></label><label className="checkbox-row"><input type="checkbox" checked={rights.manageResidents} onChange={() => toggleRight("manageResidents")} /><span>Рассматривать заявки жильцов и выдавать доступ</span></label><label className="checkbox-row"><input type="checkbox" checked={rights.manageIssues} onChange={() => toggleRight("manageIssues")} /><span>Вести проблемы и отвечать официально</span></label></div><button type="submit" className="button button--primary button--wide management-submit" disabled={busy}>Сохранить назначение</button></form></section>}
      </div>}

      {tab === "houses" && <div className="management-stack">
        <section className="panel form-panel"><div className="section-heading"><h2>Дома УК</h2><span className="count-badge">{houses.length}</span></div>{houses.length === 0 && <p className="muted-text">Дома пока не подключены.</p>}<div className="management-list">{houses.map((house) => <article className="management-item" key={house.id}><span className="small-icon"><Icon name="building" /></span><div><strong>{house.address}</strong><p>{house.company}</p></div></article>)}</div></section>
        {!permissions.manageStaff && <p className="field-help">У вас нет права отправлять заявки на подключение домов. Просмотр домов и своих заявок доступен.</p>}
        {permissions.manageStaff && <section className="panel form-panel"><h2>Запросить подключение дома</h2><p className="section-description">Дом добавит поддержка после проверки отдельной заявки.</p><form onSubmit={(event) => { event.preventDefault(); void run(() => issuesClient.createHouseRequest(newHouseAddress, undefined, newHouseNote, companyId), () => { setNewHouseAddress(""); setNewHouseNote(""); }, isDemoMode ? "Заявка сохранена только в браузере" : "Заявка отправлена поддержке"); }}><label className="field"><span>Адрес</span><input required value={newHouseAddress} onChange={(event) => setNewHouseAddress(event.target.value)} placeholder="Город, улица, дом" /></label><label className="field"><span>Пояснение</span><textarea rows={2} value={newHouseNote} onChange={(event) => setNewHouseNote(event.target.value)} placeholder="Дополнительные сведения" /></label><button type="submit" className="button button--primary button--wide management-submit" disabled={busy}>{isDemoMode ? "Сохранить пробную заявку" : "Отправить заявку"}</button></form></section>}
        <section className="panel form-panel">
          <div className="section-heading"><h2>Заявки на дома</h2><span className="count-badge">{houseTotal}</span></div>
          {houseLoading && <p className="muted-text">Загружаем заявки…</p>}
          {houseError && <p className="form-error" role="alert">{houseError}</p>}
          {!houseLoading && !houseError && houseTotal === 0 && <p className="muted-text">Ваших заявок пока нет.</p>}
          <div className="management-list">{visibleHouseRequests.map((request) => <article className="management-item" key={request.id}><div><strong>{request.address}</strong><p>{request.explanation}</p><small>{accessRequestStatusLabels[request.status as AccessRequestStatus] ?? request.status} · {formatDate(request.createdAt)}</small></div><button type="button" className="button button--soft" aria-expanded={selectedCase?.kind === "house_addition" && selectedCase.id === request.id} onClick={() => toggleCase("house_addition", request.id)}>Обсуждение</button></article>)}</div>
          {selectedCase?.kind === "house_addition" && visibleHouseRequests.some((item) => item.id === selectedCase.id) && <AccessCasePanel key={selectedCase.id} kind="house_addition" id={selectedCase.id} perspective="applicant" onChanged={refreshRequests} />}
          <RequestPager total={houseTotal} offset={houseOffset} loading={houseLoading} onPage={(next) => { setHouseOffset(next); setSelectedCase(null); }} />
        </section>
        {!isDemoMode && <section className="panel form-panel">
          <div className="section-heading"><h2>Мои обращения о регистрации УК</h2><span className="count-badge">{companyTotal}</span></div>
          {companyLoading && <p className="muted-text">Загружаем обращения…</p>}
          {companyRequestsError && <p className="form-error" role="alert">{companyRequestsError}</p>}
          {!companyLoading && !companyRequestsError && companyTotal === 0 && <p className="muted-text">Обращений пока нет.</p>}
          <div className="management-list">{companyRequests.map((request) => <article className="management-item" key={request.id}><div><strong>{request.proposed_company_name || "Регистрация УК"}</strong><small>{accessRequestStatusLabels[request.status] ?? request.status} · {formatDate(request.created_at)}</small></div><button type="button" className="button button--soft" aria-expanded={selectedCase?.kind === "company_registration" && selectedCase.id === request.id} onClick={() => toggleCase("company_registration", request.id)}>Обсуждение</button></article>)}</div>
          {selectedCase?.kind === "company_registration" && companyRequests.some((item) => item.id === selectedCase.id) && <AccessCasePanel key={selectedCase.id} kind="company_registration" id={selectedCase.id} perspective="applicant" onChanged={refreshCompanyRequests} />}
          <RequestPager total={companyTotal} offset={companyOffset} loading={companyLoading} onPage={(next) => { setCompanyOffset(next); setSelectedCase(null); }} />
        </section>}
      </div>}
    </div>
  );
}
