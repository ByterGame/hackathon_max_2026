import { useEffect, useState } from "react";

import {
  accessRequestStatusLabels,
  addAccessDiscussionMessage,
  changeAccessRequestStatus,
  decideCompanyRegistration,
  decideHouseAddition,
  getAccessRequest,
  listAccessRequestsPage,
  requestAccessCancellation,
  resolveAccessCancellation,
  updateHouseDetails,
  type AccessRequestDetail,
  type AccessRequestsPage,
} from "../features/issues/integrations/access_actions_api";
import { formatDate, formatHouseCounts } from "../features/issues/types";
import { HttpError } from "../shared/base_http_client";
import { Icon } from "../shared/common_ui/Icon";
import { ScreenHeader } from "../shared/common_ui/ScreenHeader";

type SupportKind = "company_registration" | "house_addition";
type WorkingStatus = "open" | "reviewing" | "needs_info";
type Decision = "approved" | "rejected";

const PAGE_SIZE = 20;

function positiveCount(value: string): number | null {
  const count = Number(value);
  return value.trim() && Number.isSafeInteger(count) && count > 0 ? count : null;
}

function titleOf(item: AccessRequestDetail): string {
  return item.kind === "company_registration"
    ? item.proposed_company_name || "Регистрация УК"
    : item.entered_address || "Подключение дома";
}

function participantName(item: AccessRequestDetail["discussion"][number], detail: AccessRequestDetail, actorId: string): string {
  if (item.author_user_id === actorId) return "Вы";
  if (item.author_user_id === detail.applicant_user_id) return "Заявитель";
  if (item.author_kind === "admin") return "Администратор";
  return "Поддержка";
}

export function SupportHome({ actorId, initialRequest, onNotifications, unreadCount }: { actorId: string; initialRequest?: { kind: SupportKind; id: string } | null; onNotifications?: () => void; unreadCount?: number }) {
  const [kind, setKind] = useState<SupportKind>(initialRequest?.kind ?? "company_registration");
  const [offset, setOffset] = useState(0);
  const [refreshKey, setRefreshKey] = useState(0);
  const [page, setPage] = useState<AccessRequestsPage | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(initialRequest?.id ?? null);
  const [detail, setDetail] = useState<AccessRequestDetail | null>(null);
  const [loadingList, setLoadingList] = useState(true);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [busy, setBusy] = useState(false);
  const [listError, setListError] = useState("");
  const [detailError, setDetailError] = useState("");
  const [actionError, setActionError] = useState("");
  const [notice, setNotice] = useState("");
  const [workingStatus, setWorkingStatus] = useState<WorkingStatus>("open");
  const [decision, setDecision] = useState<Decision>("approved");
  const [decisionNote, setDecisionNote] = useState("");
  const [companyName, setCompanyName] = useState("");
  const [address, setAddress] = useState("");
  const [entranceCount, setEntranceCount] = useState("");
  const [apartmentCount, setApartmentCount] = useState("");
  const [correctedEntranceCount, setCorrectedEntranceCount] = useState("");
  const [correctedApartmentCount, setCorrectedApartmentCount] = useState("");
  const [message, setMessage] = useState("");

  useEffect(() => {
    let cancelled = false;
    setLoadingList(true);
    setListError("");
    void listAccessRequestsPage(kind, offset, PAGE_SIZE, undefined, true)
      .then((result) => { if (!cancelled) setPage(result); })
      .catch((error) => { if (!cancelled) { setPage(null); setListError(error instanceof HttpError && error.status === 403 ? "Нет доступа к этому разделу" : error instanceof Error ? error.message : "Не удалось загрузить обращения"); } })
      .finally(() => { if (!cancelled) setLoadingList(false); });
    return () => { cancelled = true; };
  }, [kind, offset, refreshKey]);

  useEffect(() => {
    if (!selectedId) { setDetail(null); return; }
    let cancelled = false;
    setLoadingDetail(true);
    setDetailError("");
    void getAccessRequest(kind, selectedId)
      .then((item) => {
        if (cancelled) return;
        setDetail(item);
        setWorkingStatus(item.status === "reviewing" || item.status === "needs_info" ? item.status : "open");
        setCompanyName(item.proposed_company_name ?? "");
        setAddress(item.entered_address ?? "");
        setEntranceCount(item.entrance_count == null ? "" : String(item.entrance_count));
        setApartmentCount(item.apartment_count == null ? "" : String(item.apartment_count));
        setCorrectedEntranceCount(item.entrance_count == null ? "" : String(item.entrance_count));
        setCorrectedApartmentCount(item.apartment_count == null ? "" : String(item.apartment_count));
      })
      .catch((error) => { if (!cancelled) { setDetail(null); setDetailError(error instanceof HttpError && error.status === 403 ? "Нет доступа к этой заявке" : error instanceof Error ? error.message : "Не удалось открыть заявку"); } })
      .finally(() => { if (!cancelled) setLoadingDetail(false); });
    return () => { cancelled = true; };
  }, [kind, selectedId, refreshKey]);

  useEffect(() => {
    if (!selectedId) return;
    const frame = requestAnimationFrame(() => document.getElementById("support-request-detail")?.scrollIntoView({ behavior: "smooth", block: "start" }));
    return () => cancelAnimationFrame(frame);
  }, [selectedId]);

  function selectKind(nextKind: SupportKind) {
    if (nextKind === kind) return;
    setKind(nextKind);
    setOffset(0);
    setSelectedId(null);
    setNotice("");
    setActionError("");
  }

  function selectRequest(id: string) {
    setSelectedId((current) => current === id ? null : id);
    setDecision("approved");
    setDecisionNote("");
    setEntranceCount("");
    setApartmentCount("");
    setCorrectedEntranceCount("");
    setCorrectedApartmentCount("");
    setMessage("");
    setNotice("");
    setActionError("");
  }

  async function run(action: () => Promise<unknown>, success: string, after?: () => void) {
    setBusy(true);
    setActionError("");
    setNotice("");
    try {
      await action();
      after?.();
      setNotice(success);
      setRefreshKey((current) => current + 1);
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "Не удалось сохранить изменение");
    } finally {
      setBusy(false);
    }
  }

  const active = detail && detail.status !== "closed" && detail.status !== "cancelled";
  const cancellationByApplicant = detail?.cancel_requested_by === detail?.applicant_user_id;
  const canResolveCancellation = Boolean(active && detail?.cancel_requested_by && cancellationByApplicant);
  const totalPages = Math.max(1, Math.ceil((page?.total ?? 0) / PAGE_SIZE));

  return <div className="page page--employee-access">
    <ScreenHeader title="Поддержка" subtitle="Обращения управляющих компаний и домов" icon="shield" action={onNotifications ? { label: "Уведомления", onClick: onNotifications, icon: "bell", badge: unreadCount } : undefined} />
    <div className="segmented" role="tablist" aria-label="Вид обращения">
      <button type="button" role="tab" aria-selected={kind === "company_registration"} className={kind === "company_registration" ? "is-active" : ""} onClick={() => selectKind("company_registration")}>Регистрация УК</button>
      <button type="button" role="tab" aria-selected={kind === "house_addition"} className={kind === "house_addition" ? "is-active" : ""} onClick={() => selectKind("house_addition")}>Подключение домов</button>
    </div>
    <div className="management-stack">
    <section className="panel form-panel">
      <div className="section-heading"><div><h2>Обращения</h2><p>{page ? `Всего: ${page.total}` : "Загружаем…"}</p></div><button type="button" className="button button--soft" disabled={loadingList || busy} onClick={() => setRefreshKey((current) => current + 1)}>Обновить</button></div>
      {listError && <p className="form-error" role="alert">{listError}</p>}
      {loadingList && <p className="muted-text">Загружаем обращения…</p>}
      {!loadingList && page?.items.length === 0 && <p className="muted-text">Обращений пока нет.</p>}
      {!loadingList && page && <div className="management-list">{page.items.map((item) => <article className="management-item" key={item.id}>
        <div><strong>{titleOf(item)}</strong><p>{item.kind === "company_registration" ? item.free_text : formatHouseCounts(item.entrance_count, item.apartment_count)}</p><small>{formatDate(item.created_at)} · {accessRequestStatusLabels[item.status]}</small></div>
        <button type="button" className="button button--soft" aria-expanded={selectedId === item.id} onClick={() => selectRequest(item.id)}>{selectedId === item.id ? "Скрыть" : "Открыть"}</button>
      </article>)}</div>}
      {page && page.total > PAGE_SIZE && <div className="admin-pagination"><button type="button" className="button button--soft" disabled={loadingList || offset === 0} onClick={() => { setOffset(Math.max(0, offset - PAGE_SIZE)); setSelectedId(null); }}>Назад</button><span>Страница {Math.floor(offset / PAGE_SIZE) + 1} из {totalPages}</span><button type="button" className="button button--soft" disabled={loadingList || offset + PAGE_SIZE >= page.total} onClick={() => { setOffset(offset + PAGE_SIZE); setSelectedId(null); }}>Далее</button></div>}
    </section>

    {selectedId && <section className="panel form-panel" id="support-request-detail">
      {loadingDetail && <p className="muted-text">Загружаем заявку…</p>}
      {detailError && <p className="form-error" role="alert">{detailError}</p>}
      {detail && <>
        <div className="section-heading"><div><h2>{titleOf(detail)}</h2><p>{accessRequestStatusLabels[detail.status]} · {formatDate(detail.created_at)}</p></div><button type="button" className="icon-button icon-button--soft" aria-label="Закрыть заявку" onClick={() => setSelectedId(null)}><Icon name="close" size={18} /></button></div>
        {detail.kind === "company_registration" ? <><p><strong>Номер первого сотрудника:</strong> {detail.phone_number}</p><p><strong>Описание:</strong> {detail.free_text}</p></> : <><p><strong>Адрес:</strong> {detail.entered_address}</p><p>{formatHouseCounts(detail.entrance_count, detail.apartment_count)}</p><p><strong>Описание:</strong> {detail.free_text || "Не указано"}</p></>}
        {detail.outcome && <p><strong>Решение:</strong> {detail.outcome === "approved" ? "Одобрено" : "Отклонено"}</p>}
        {detail.decision_note && <p><strong>Пояснение:</strong> {detail.decision_note}</p>}
        {active && <div className="decision-box"><h3>Рабочий статус</h3><div className="button-row"><label className="field"><span>Состояние</span><select value={workingStatus} onChange={(event) => setWorkingStatus(event.target.value as WorkingStatus)}><option value="open">Открыта</option><option value="reviewing">На рассмотрении</option><option value="needs_info">Нужны уточнения</option></select></label><button type="button" className="button button--soft" disabled={busy || workingStatus === detail.status} onClick={() => void run(() => changeAccessRequestStatus(kind, detail.id, workingStatus), "Статус сохранён")}>Сохранить статус</button></div></div>}
        {active && <div className="decision-box">
          <h3>Решение по обращению</h3>
          <label className="field"><span>Результат</span><select value={decision} onChange={(event) => setDecision(event.target.value as Decision)}><option value="approved">Одобрить</option><option value="rejected">Отклонить</option></select></label>
          {decision === "approved" && (kind === "company_registration" ? <label className="field"><span>Название УК</span><input required value={companyName} onChange={(event) => setCompanyName(event.target.value)} /></label> : <>
            <label className="field"><span>Адрес дома</span><input required value={address} onChange={(event) => setAddress(event.target.value)} /></label>
            <div className="field-grid">
              <label className="field"><span>Количество подъездов</span><input required type="number" min="1" value={entranceCount} onChange={(event) => setEntranceCount(event.target.value)} /></label>
              <label className="field"><span>Количество квартир</span><input required type="number" min="1" value={apartmentCount} onChange={(event) => setApartmentCount(event.target.value)} /></label>
            </div>
          </>)}
          <label className="field"><span>Пояснение · обязательно</span><textarea value={decisionNote} onChange={(event) => setDecisionNote(event.target.value)} rows={3} /></label>
          <button type="button" className="button button--primary management-submit" disabled={busy || !decisionNote.trim() || decision === "approved" && (kind === "company_registration" ? !companyName.trim() : !address.trim() || positiveCount(entranceCount) === null || positiveCount(apartmentCount) === null)} onClick={() => void run(() => kind === "company_registration" ? decideCompanyRegistration(detail.id, decision, decisionNote, companyName) : decideHouseAddition(detail.id, decision, decisionNote, address, positiveCount(entranceCount) ?? undefined, positiveCount(apartmentCount) ?? undefined), "Решение сохранено", () => setDecisionNote(""))}>Сохранить решение</button>
        </div>}
        {kind === "house_addition" && detail.outcome === "approved" && detail.resolved_house_id && <div className="decision-box">
          <h3>Исправить данные подключённого дома</h3>
          <p className="field-help">Исправления сохранятся в доме и заявке. История изменений останется в журнале.</p>
          <div className="field-grid">
            <label className="field"><span>Количество подъездов</span><input required type="number" min="1" value={correctedEntranceCount} onChange={(event) => setCorrectedEntranceCount(event.target.value)} /></label>
            <label className="field"><span>Количество квартир</span><input required type="number" min="1" value={correctedApartmentCount} onChange={(event) => setCorrectedApartmentCount(event.target.value)} /></label>
          </div>
          <button type="button" className="button button--primary management-submit" disabled={busy || positiveCount(correctedEntranceCount) === null || positiveCount(correctedApartmentCount) === null || Number(correctedEntranceCount) === detail.entrance_count && Number(correctedApartmentCount) === detail.apartment_count} onClick={() => { const entrance = positiveCount(correctedEntranceCount); const apartments = positiveCount(correctedApartmentCount); const houseId = detail.resolved_house_id; if (entrance !== null && apartments !== null && houseId) void run(() => updateHouseDetails(houseId, entrance, apartments), "Данные дома исправлены"); }}>Сохранить исправления</button>
        </div>}
        {active && <div className="decision-box"><h3>Отмена заявки</h3>{detail.cancel_requested_by ? canResolveCancellation ? <><p>Заявитель просит отменить обращение.</p><div className="button-row"><button type="button" className="button button--soft" disabled={busy} onClick={() => void run(() => resolveAccessCancellation(kind, detail.id, false), "Запрос на отмену отклонён")}>Оставить заявку</button><button type="button" className="button button--primary" disabled={busy} onClick={() => void run(() => resolveAccessCancellation(kind, detail.id, true), "Заявка отменена")}>Подтвердить отмену</button></div></> : <p>Запрос на отмену ожидает ответа заявителя.</p> : <button type="button" className="button button--soft" disabled={busy} onClick={() => void run(() => requestAccessCancellation(kind, detail.id), "Запрос на отмену отправлен")}>Запросить отмену</button>}</div>}
        <div className="decision-box"><div className="section-heading"><h3>Обсуждение</h3><span className="count-badge">{detail.discussion.length}</span></div><div className="message-list">{detail.discussion.length ? detail.discussion.map((item) => <article className="message" key={item.id}><span className="message__avatar"><Icon name="chat" size={19} /></span><div><div className="message__heading"><strong>{participantName(item, detail, actorId)}</strong><time>{formatDate(item.created_at)}</time></div><p>{item.text}</p></div></article>) : <p className="muted-text">Сообщений пока нет.</p>}</div>{detail.status !== "cancelled" && <form className="comment-form" onSubmit={(event) => { event.preventDefault(); if (message.trim()) void run(() => addAccessDiscussionMessage(kind, detail.id, message), "Сообщение отправлено", () => setMessage("")); }}><input aria-label="Сообщение в обсуждении заявки" value={message} onChange={(event) => setMessage(event.target.value)} placeholder="Написать сообщение…" /><button type="submit" className="icon-button icon-button--blue" aria-label="Отправить сообщение" disabled={busy || !message.trim()}><Icon name="send" size={19} /></button></form>}</div>
        {notice && <p className="form-success" role="status">{notice}</p>}
        {actionError && <p className="form-error" role="alert">{actionError}</p>}
      </>}
    </section>}
    </div>
  </div>;
}
