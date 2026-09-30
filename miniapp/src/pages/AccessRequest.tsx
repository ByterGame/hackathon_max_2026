import { useEffect, useRef, useState } from "react";

import { updateProfileName } from "../features/auth/integrations/client_api";
import { getDraft, listDrafts, saveDraft, submitDraft, type DraftData } from "../features/drafts/integrations/client_api";
import { accessRequestStatusLabels, listAccessRequestsPage, requestAccessCancellation, type AccessRequestDetail, type AccessRequestsPage } from "../features/issues/integrations/access_actions_api";
import { isDemoMode, issuesClient } from "../features/issues/integrations/client_api";
import { demoResident, formatApartmentLocation, formatDate, type House, type ResidentOffer, type ResidentRequest } from "../features/issues/types";
import { NotificationToggle } from "../features/notifications/ui/NotificationToggle";
import { HttpError } from "../shared/base_http_client";
import { Icon } from "../shared/common_ui/Icon";
import { ScreenHeader } from "../shared/common_ui/ScreenHeader";
import { AccessCasePanel } from "./EmployeeAccess";

const PAGE_SIZE = 20;

function residentRequestFromPage(item: AccessRequestDetail): ResidentRequest {
  return {
    id: item.id, houseId: item.house_id ?? "", address: item.address_display,
    fullName: item.submitted_full_name ?? "", entrance: item.submitted_entrance_number ?? undefined,
    apartment: item.submitted_apartment_number ?? 0, status: item.status,
    outcome: item.outcome === "granted" || item.outcome === "denied" ? item.outcome : undefined,
    decisionNote: item.decision_note ?? undefined, createdAt: item.created_at,
  };
}

export function AccessRequest({ houses, requests, offers, initialName, nameConfirmed, onBack, onChanged, onAccountChanged, onOpenRequest }: { houses: House[]; requests: ResidentRequest[]; offers: ResidentOffer[]; initialName?: string; nameConfirmed: boolean; onBack: () => void; onChanged: () => Promise<void>; onAccountChanged: () => Promise<void>; onOpenRequest: (id: string) => void }) {
  const [search, setSearch] = useState("");
  const [matches, setMatches] = useState<House[]>(isDemoMode ? houses : []);
  const [selectedHouse, setSelectedHouse] = useState<House | null>(null);
  const [fullName, setFullName] = useState(initialName ?? (isDemoMode ? demoResident.name : ""));
  const [nameEditorOpen, setNameEditorOpen] = useState(false);
  const [profileNameDraft, setProfileNameDraft] = useState("");
  const [entrance, setEntrance] = useState("");
  const [apartment, setApartment] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState(false);
  const [selectedRequestId, setSelectedRequestId] = useState("");
  const [offerOffset, setOfferOffset] = useState(0);
  const [requestOffset, setRequestOffset] = useState(0);
  const [requestRevision, setRequestRevision] = useState(0);
  const [requestPage, setRequestPage] = useState<AccessRequestsPage | null>(null);
  const [requestLoading, setRequestLoading] = useState(!isDemoMode);
  const [requestError, setRequestError] = useState("");
  const [sharedDraft, setSharedDraft] = useState<DraftData | null>(null);
  const sharedDraftRef = useRef<DraftData | null>(null);
  const draftSaveInFlight = useRef<Promise<DraftData> | null>(null);
  const savedHouseId = useRef<string | null>(null);
  const editedByUser = useRef(false);
  const createdRequestId = useRef<string | null>(null);
  const createKey = useRef<string | null>(null);
  const [draftNotice, setDraftNotice] = useState("");
  const myOffers = isDemoMode ? offers.filter((item) => item.phone === demoResident.phone) : offers;
  const visibleOffers = myOffers.slice(offerOffset, offerOffset + PAGE_SIZE);
  const visibleRequests = isDemoMode ? requests.slice(requestOffset, requestOffset + PAGE_SIZE) : requestPage?.items.map(residentRequestFromPage) ?? [];
  const requestTotal = isDemoMode ? requests.length : requestPage?.total ?? 0;

  useEffect(() => {
    if (nameConfirmed && initialName) setFullName(initialName);
  }, [nameConfirmed, initialName]);

  useEffect(() => {
    if (offerOffset > 0 && offerOffset >= myOffers.length) {
      setOfferOffset(Math.max(0, Math.floor((myOffers.length - 1) / PAGE_SIZE) * PAGE_SIZE));
    }
  }, [offerOffset, myOffers.length]);

  useEffect(() => {
    if (isDemoMode && requestOffset > 0 && requestOffset >= requests.length) {
      setRequestOffset(Math.max(0, Math.floor((requests.length - 1) / PAGE_SIZE) * PAGE_SIZE));
    }
  }, [requestOffset, requests.length]);

  useEffect(() => {
    if (isDemoMode) return;
    let cancelled = false;
    setRequestLoading(true); setRequestError(""); setRequestPage(null);
    void listAccessRequestsPage("resident", requestOffset, PAGE_SIZE)
      .then((page) => {
        if (cancelled) return;
        if (requestOffset > 0 && requestOffset >= page.total) {
          setRequestOffset(Math.max(0, Math.floor((page.total - 1) / PAGE_SIZE) * PAGE_SIZE));
        } else {
          setRequestPage(page);
        }
      })
      .catch((reason) => { if (!cancelled) setRequestError(reason instanceof HttpError && reason.status === 403 ? "Нет доступа к своим заявкам" : reason instanceof Error ? reason.message : "Не удалось загрузить заявки"); })
      .finally(() => { if (!cancelled) setRequestLoading(false); });
    return () => { cancelled = true; };
  }, [requestOffset, requestRevision]);

  async function refreshRequests() {
    await onChanged();
    setRequestRevision((current) => current + 1);
  }

  useEffect(() => {
    if (isDemoMode) return;
    let active = true;
    void listDrafts("resident_request").then(async (items) => {
      const listed = items.find((item) => !item.submitted_at);
      if (!listed) return;
      const saved = await getDraft(listed.id);
      if (!active) return;
      sharedDraftRef.current = saved;
      setSharedDraft(saved);
      const payload = saved.payload;
      savedHouseId.current = typeof payload.house_id === "string" ? payload.house_id : null;
      if (editedByUser.current) return;
      if (!nameConfirmed && typeof payload.full_name === "string") setFullName(payload.full_name);
      if (typeof payload.entrance_number === "number") setEntrance(String(payload.entrance_number));
      if (typeof payload.apartment_number === "number") setApartment(String(payload.apartment_number));
      setDraftNotice("Черновик восстановлен. Найдите и подтвердите адрес дома заново перед отправкой.");
    }).catch(() => { if (active) setDraftNotice("Не удалось загрузить общий черновик. Можно продолжить без него."); });
    return () => { active = false; };
  }, []);

  function draftPayload(): Record<string, unknown> {
    const houseId = selectedHouse?.id ?? savedHouseId.current;
    const entranceNumber = Number(entrance);
    const apartmentNumber = Number(apartment);
    return {
      ...(houseId ? { house_id: houseId } : {}),
      ...(fullName.trim() ? { full_name: fullName.trim() } : {}),
      ...(Number.isInteger(entranceNumber) && entranceNumber > 0 ? { entrance_number: entranceNumber } : {}),
      ...(Number.isInteger(apartmentNumber) && apartmentNumber > 0 ? { apartment_number: apartmentNumber } : {}),
    };
  }

  async function persistDraft(automatic = false): Promise<DraftData | null> {
    if (isDemoMode) return null;
    if (draftSaveInFlight.current) await draftSaveInFlight.current;
    const request = saveDraft("resident_request", draftPayload(), sharedDraftRef.current);
    draftSaveInFlight.current = request;
    let saved: DraftData;
    try { saved = await request; }
    finally { if (draftSaveInFlight.current === request) draftSaveInFlight.current = null; }
    sharedDraftRef.current = saved;
    setSharedDraft(saved);
    if (!automatic) setDraftNotice("Черновик сохранён. Его можно продолжить в боте MAX.");
    return saved;
  }

  useEffect(() => {
    if (isDemoMode || !editedByUser.current || busy || success) return;
    const timer = window.setTimeout(() => {
      void persistDraft(true).catch((reason) => setDraftNotice(`Автосохранение не удалось: ${reason instanceof Error ? reason.message : "ошибка сервера"}`));
    }, 900);
    return () => window.clearTimeout(timer);
  }, [selectedHouse?.id, fullName, entrance, apartment, busy, success]);

  async function saveManually() {
    setBusy(true); setError("");
    try { await persistDraft(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Не удалось сохранить черновик"); }
    finally { setBusy(false); }
  }

  async function saveProfileName() {
    const name = profileNameDraft.trim();
    if (!name) { setError("Укажите ФИО"); return; }
    setBusy(true); setError("");
    try {
      const profile = await updateProfileName(name);
      setFullName(profile.full_name);
      setNameEditorOpen(false);
      const refreshed = await Promise.allSettled([onAccountChanged(), onChanged()]);
      setDraftNotice(refreshed.some((result) => result.status === "rejected")
        ? "ФИО изменено. Не удалось обновить экран — перезагрузите приложение."
        : "ФИО изменено в общем профиле и незакрытых заявках.");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Не удалось изменить ФИО"); }
    finally { setBusy(false); }
  }

  useEffect(() => {
    if (search.trim().length < 2) { setMatches(isDemoMode ? houses : []); return; }
    let cancelled = false;
    const timer = window.setTimeout(() => {
      void issuesClient.searchHouses(search).then((items) => { if (!cancelled) setMatches(items); }).catch((reason) => { if (!cancelled) setError(reason instanceof Error ? reason.message : "Не удалось найти дома"); });
    }, 250);
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [search, houses]);

  async function answerOffer(id: string, accept: boolean) {
    setBusy(true); setError("");
    try { await issuesClient.answerResidentOffer(id, accept); await onChanged(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Не удалось ответить на предложение"); }
    finally { setBusy(false); }
  }

  async function cancelOpenRequest(id: string) {
    if (!window.confirm("Отменить заявку на доступ к дому?")) return;
    setBusy(true); setError("");
    try { await requestAccessCancellation("resident", id); await refreshRequests(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Не удалось отменить заявку"); }
    finally { setBusy(false); }
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (nameEditorOpen) { setError("Сохраните или отмените изменение ФИО перед подачей заявки"); return; }
    if (!fullName.trim()) { setError("Укажите ФИО для общего профиля"); return; }
    if (!selectedHouse || !confirmed) { setError("Выберите и подтвердите адрес дома"); return; }
    const entranceNumber = Number(entrance);
    if (!Number.isInteger(entranceNumber) || entranceNumber < 1) { setError("Укажите корректный номер подъезда"); return; }
    if (selectedHouse.entranceCount && entranceNumber > selectedHouse.entranceCount) {
      setError(`В этом доме ${selectedHouse.entranceCount} подъездов. Проверьте номер.`);
      return;
    }
    const apartmentNumber = Number(apartment);
    if (!Number.isInteger(apartmentNumber) || apartmentNumber < 1) { setError("Укажите корректный номер квартиры"); return; }
    if (!createdRequestId.current && requests.some((item) => item.houseId === selectedHouse.id && item.apartment === apartmentNumber && !["closed", "cancelled"].includes(item.status))) {
      setError("Заявка на эту квартиру уже рассматривается. Если подъезд указан неверно, исправьте существующую заявку.");
      return;
    }
    setBusy(true); setError("");
    try {
      let saved = sharedDraftRef.current;
      if (!isDemoMode && !createdRequestId.current) saved = await persistDraft();
      if (!createdRequestId.current) {
        if (!createKey.current && !isDemoMode) createKey.current = crypto.randomUUID();
        const request = await issuesClient.submitResidentRequest({ houseId: selectedHouse.id, fullName, entrance: entranceNumber, apartment: apartmentNumber }, createKey.current ?? undefined);
        createdRequestId.current = request.id;
      }
      if (!isDemoMode && saved && !saved.submitted_at) {
        let submitted: DraftData;
        try { submitted = await submitDraft(saved); }
        catch (reason) {
          const latest = await getDraft(saved.id).catch(() => null);
          if (!latest?.submitted_at) throw reason;
          submitted = latest;
        }
        sharedDraftRef.current = submitted;
        setSharedDraft(submitted);
      }
      await refreshRequests();
      if (!isDemoMode) await onAccountChanged().catch(() => setDraftNotice("Заявка отправлена, но данные профиля не обновились на экране. Перезагрузите приложение."));
      setSuccess(true);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Не удалось подать заявку"); }
    finally { setBusy(false); }
  }

  return (
    <div className="page page--form">
      <ScreenHeader title="Доступ к дому" subtitle="Заявка жильца" onBack={onBack} />
      <div className="info-panel"><Icon name="info" size={20} /> Дом и квартиру подтверждает сотрудник УК. Номер телефона сам по себе не доказывает проживание.</div>
      {success ? <section className="panel success-panel"><span className="success-icon"><Icon name="check" size={32} /></span><h2>{isDemoMode ? "Заявка сохранена в демо" : "Заявка отправлена в УК"}</h2><p>{isDemoMode ? "Заявка находится только в этом браузере." : "Следите за статусом и пишите УК в карточке заявки."} До выдачи доступа проблемы этого дома недоступны.</p>{createdRequestId.current && <button className="button button--primary" type="button" onClick={() => { if (createdRequestId.current) onOpenRequest(createdRequestId.current); }}>{isDemoMode ? "Открыть заявку" : "Открыть заявку и обсуждение"}</button>}<button className="button button--soft" type="button" onClick={onBack}>Вернуться</button></section> : <form className="form-stack" onInputCapture={() => { editedByUser.current = true; }} onSubmit={(event) => void submit(event)}>
        <fieldset className="request-form-fields" disabled={Boolean(createdRequestId.current)}>
        <section className="panel form-panel"><h2>Найдите подключённый дом</h2><label className="field"><span>Введите улицу и номер дома</span><input value={search} onChange={(event) => { setSearch(event.target.value); setSelectedHouse(null); setConfirmed(false); }} placeholder="Например: Пушкина, 5" /></label><label className="field"><span>Совпадающие адреса</span><select required value={selectedHouse?.id ?? ""} onChange={(event) => { const house = matches.find((item) => item.id === event.target.value) ?? null; setSelectedHouse(house); savedHouseId.current = house?.id ?? null; setConfirmed(false); }}><option value="">Выберите адрес</option>{matches.map((item) => <option key={item.id} value={item.id}>{item.address}</option>)}</select></label>{sharedDraft && !selectedHouse && <p className="field-help">В черновике сохранён дом, но адрес нужно найти и подтвердить повторно.</p>}{search.trim().length >= 2 && matches.length === 0 && <p className="field-help">Подключённый дом не найден. Проверьте адрес или попробуйте позже.</p>}{selectedHouse && <label className="checkbox-row"><input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} /><span>Подтверждаю адрес: <strong>{selectedHouse.address}</strong></span></label>}</section>
        <section className="panel form-panel"><h2>Ваши данные</h2>{nameConfirmed ? <div className="field"><span>ФИО в общем профиле</span><strong>{fullName}</strong><p className="field-help">Оно используется для заявок на доступ ко всем домам.</p>{!isDemoMode && !nameEditorOpen && <button type="button" className="button button--soft" onClick={() => { setProfileNameDraft(fullName); setNameEditorOpen(true); }}>Изменить ФИО</button>}{!isDemoMode && nameEditorOpen && <><input value={profileNameDraft} onChange={(event) => setProfileNameDraft(event.target.value)} placeholder="Иванов Иван Иванович" maxLength={255} /><div className="button-row"><button type="button" className="button button--primary" disabled={busy} onClick={() => void saveProfileName()}>Сохранить ФИО</button><button type="button" className="button button--soft" disabled={busy} onClick={() => setNameEditorOpen(false)}>Отмена</button></div></>}</div> : <label className="field"><span>ФИО для общего профиля</span><input required value={fullName} onChange={(event) => setFullName(event.target.value)} placeholder="Иванов Иван Иванович" maxLength={255} /><span className="field-help">Проверьте ФИО: после отправки оно сохранится для всех домов.</span></label>}<div className="field-grid"><label className="field"><span>Подъезд</span><input required type="number" min="1" max={selectedHouse?.entranceCount ?? undefined} inputMode="numeric" value={entrance} onChange={(event) => setEntrance(event.target.value)} placeholder="2" />{selectedHouse?.entranceCount && <span className="field-help">От 1 до {selectedHouse.entranceCount}</span>}</label><label className="field"><span>Квартира</span><input required type="number" min="1" inputMode="numeric" value={apartment} onChange={(event) => setApartment(event.target.value)} placeholder="24" /></label></div></section>
        </fieldset>
        {!isDemoMode && <button type="button" className="button button--soft button--wide" disabled={busy} onClick={() => void saveManually()}>{sharedDraft ? "Обновить общий черновик" : "Сохранить общий черновик"}</button>}
        {draftNotice && <p className="draft-notice" role="status">{draftNotice}</p>}
        {createdRequestId.current && <p className="draft-notice">Заявка уже подана. Повторное нажатие завершит сохранение черновика, не создавая вторую заявку.</p>}
        {error && <p className="form-error" role="alert">{error}</p>}
        <button className="button button--primary button--wide" type="submit" disabled={busy}>{busy ? "Отправляем…" : createdRequestId.current ? "Завершить подачу" : isDemoMode ? "Подать заявку в демо" : "Отправить заявку в УК"}</button>
      </form>}
      {myOffers.length > 0 && <section className="requests-list"><div className="section-heading"><h2>Предложения от УК</h2><span className="count-badge">{myOffers.length}</span></div>{visibleOffers.map((offer) => <div className="panel request-card" key={offer.id}><strong>{houses.find((item) => item.id === offer.houseId)?.address ?? "Дом"}</strong><span>{formatApartmentLocation(offer.apartment, offer.entrance)}</span><small>{offer.status === "pending" ? "Ожидает вашего ответа" : offer.status === "accepted" ? "Принято, доступ выдан" : "Отклонено"}</small>{offer.status === "pending" && <div className="button-row"><button type="button" className="button button--soft" disabled={busy} onClick={() => void answerOffer(offer.id, false)}>Отклонить</button><button type="button" className="button button--primary" disabled={busy} onClick={() => void answerOffer(offer.id, true)}>Принять</button></div>}</div>)}{myOffers.length > PAGE_SIZE && <div className="admin-pagination"><button type="button" className="button button--soft" disabled={offerOffset === 0} onClick={() => setOfferOffset(Math.max(0, offerOffset - PAGE_SIZE))}>Назад</button><span>Страница {Math.floor(offerOffset / PAGE_SIZE) + 1} из {Math.ceil(myOffers.length / PAGE_SIZE)}</span><button type="button" className="button button--soft" disabled={offerOffset + PAGE_SIZE >= myOffers.length} onClick={() => setOfferOffset(offerOffset + PAGE_SIZE)}>Далее</button></div>}</section>}
      {requestLoading && <p className="muted-text">Загружаем заявки…</p>}
      {requestError && <p className="form-error" role="alert">{requestError}</p>}
      {!requestLoading && !requestError && requestTotal > 0 && <section className="requests-list"><div className="section-heading"><h2>Мои заявки</h2><span className="count-badge">{requestTotal}</span></div>{visibleRequests.map((request) => <div className="panel request-card" key={request.id}><strong>{request.address ?? houses.find((item) => item.id === request.houseId)?.address ?? "Дом по заявке"}</strong><span>{formatApartmentLocation(request.apartment, request.entrance)}</span><small>{formatDate(request.createdAt)} · {request.status === "closed" ? request.outcome === "granted" ? "Доступ выдан" : "Отказано" : accessRequestStatusLabels[request.status]}</small>{request.decisionNote && <span>Пояснение УК: {request.decisionNote}</span>}<NotificationToggle subject="resident_request" id={request.id} /><div className="button-row"><button type="button" className="button button--soft" aria-expanded={selectedRequestId === request.id} onClick={() => setSelectedRequestId((current) => current === request.id ? "" : request.id)}>{selectedRequestId === request.id ? "Скрыть обсуждение" : "Открыть заявку"}</button>{!isDemoMode && request.status === "open" && selectedRequestId !== request.id && <button type="button" className="button button--soft" disabled={busy} onClick={() => void cancelOpenRequest(request.id)}>Отменить заявку</button>}</div>{selectedRequestId === request.id && <AccessCasePanel key={request.id} kind="resident" id={request.id} perspective="applicant" onChanged={refreshRequests} />}</div>)}{requestTotal > PAGE_SIZE && <div className="admin-pagination"><button type="button" className="button button--soft" disabled={requestOffset === 0} onClick={() => { setRequestOffset(Math.max(0, requestOffset - PAGE_SIZE)); setSelectedRequestId(""); }}>Назад</button><span>Страница {Math.floor(requestOffset / PAGE_SIZE) + 1} из {Math.ceil(requestTotal / PAGE_SIZE)}</span><button type="button" className="button button--soft" disabled={requestOffset + PAGE_SIZE >= requestTotal} onClick={() => { setRequestOffset(requestOffset + PAGE_SIZE); setSelectedRequestId(""); }}>Далее</button></div>}</section>}
      {error && success && <p className="form-error" role="alert">{error}</p>}
    </div>
  );
}
