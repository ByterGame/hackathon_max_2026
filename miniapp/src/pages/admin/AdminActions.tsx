import { useEffect, useState, type FormEvent } from "react";

import { downloadAdminFile, listAdminItems, type AdminEntity, type AdminIssueCategory, type AdminPage } from "../../features/admin/integrations/client_api";

type RunAction = (action: string, payload: Record<string, unknown>, success: string) => Promise<boolean>;

function text(item: Record<string, unknown>, field: string): string {
  const value = item[field];
  return value === null || value === undefined ? "" : String(value);
}

function integer(item: Record<string, unknown>, field: string): number | null {
  const value = item[field];
  return typeof value === "number" && Number.isInteger(value) ? value : null;
}

function optional(value: string): string | null { return value.trim() || null; }

function toIso(value: string): string | null {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date.toISOString();
}

function AdminReferencePicker({ entity, value, onChange }: { entity: "companies" | "houses"; value: string; onChange: (id: string) => void }) {
  const label = entity === "companies" ? "Управляющая компания" : "Дом";
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<AdminPage | null>(null);
  const [selectedLabel, setSelectedLabel] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    const timer = window.setTimeout(() => { setOffset(0); setQuery(search.trim()); }, 250);
    return () => window.clearTimeout(timer);
  }, [search]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true); setError("");
    void listAdminItems(entity, query, offset, 20)
      .then((result) => { if (!cancelled) setPage(result); })
      .catch((reason) => { if (!cancelled) setError(reason instanceof Error ? reason.message : "Не удалось загрузить записи"); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [entity, query, offset]);

  return <div className="admin-reference">
    <label className="field"><span>Поиск: {label.toLowerCase()}</span><input type="search" maxLength={100} value={search} onChange={(event) => { setSearch(event.target.value); if (event.target.value.trim() !== query) setPage(null); }} placeholder="Название или адрес" /></label>
    <label className="field"><span>{label}</span><select required value={value} onChange={(event) => { onChange(event.target.value); setSelectedLabel(event.target.selectedOptions[0]?.textContent ?? ""); }}>
      <option value="">Выберите {entity === "companies" ? "УК" : "дом"}</option>
      {value && !page?.items.some((item) => item.id === value) && <option value={value}>{selectedLabel || value}</option>}
      {search.trim() === query && page?.items.map((item) => <option key={item.id} value={item.id}>{item.title}{item.status === "archived" ? " · архив" : ""}</option>)}
    </select></label>
    {loading && <p className="field-help">Ищем записи…</p>}
    {error && <p className="form-error" role="alert">{error}</p>}
    {page && page.total > page.limit && <div className="admin-pagination"><button type="button" className="button button--soft" disabled={loading || offset === 0} onClick={() => setOffset(Math.max(0, offset - page.limit))}>Назад</button><span>{Math.floor(offset / page.limit) + 1} / {Math.ceil(page.total / page.limit)}</span><button type="button" className="button button--soft" disabled={loading || offset + page.limit >= page.total} onClick={() => setOffset(offset + page.limit)}>Далее</button></div>}
  </div>;
}

export function AdminAddAccess({ busy, onAction }: { busy: boolean; onAction: RunAction }) {
  const [kind, setKind] = useState<"support" | "staff" | "resident">("support");
  const [phone, setPhone] = useState("");
  const [companyId, setCompanyId] = useState("");
  const [houseId, setHouseId] = useState("");
  const [apartment, setApartment] = useState("");
  const [validTo, setValidTo] = useState("");
  const [staffRights, setStaffRights] = useState({ can_manage_staff: false, can_manage_residents: false, can_manage_issues: false });

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    let success = false;
    if (kind === "support") {
      success = await onAction("invite_support", { phone_number: phone }, "Приглашение оператору создано. Доступ появится после подтверждения номера и принятия приглашения.");
    } else if (kind === "staff") {
      success = await onAction("assign_staff", { company_id: companyId, phone_number: phone, ...staffRights }, "Назначение сотрудника сохранено.");
    } else {
      success = await onAction("offer_resident", { house_id: houseId, phone_number: phone, apartment_number: Number(apartment), valid_to: toIso(validTo) }, "Предложение доступа жильцу создано. Доступ появится после принятия.");
    }
    if (success) { setPhone(""); setApartment(""); setValidTo(""); }
  }

  return <details className="panel admin-create">
    <summary><span>Добавить по номеру</span><small>Оператор, сотрудник УК или жилец</small></summary>
    <form className="form-stack" onSubmit={(event) => void submit(event)}>
      <label className="field"><span>Кому выдать доступ</span><select value={kind} onChange={(event) => setKind(event.target.value as typeof kind)}><option value="support">Оператор поддержки</option><option value="staff">Сотрудник УК</option><option value="resident">Жилец дома</option></select></label>
      <label className="field"><span>Номер телефона</span><input type="tel" required value={phone} onChange={(event) => setPhone(event.target.value)} placeholder="+7 999 123-45-67" autoComplete="tel" /></label>
      {kind === "support" && <p className="field-help">Оператор получит права только после входа с подтверждённым номером и принятия приглашения. Администратором он не станет.</p>}
      {kind === "staff" && <>
        <AdminReferencePicker entity="companies" value={companyId} onChange={setCompanyId} />
        <div className="rights-list"><label className="checkbox-row"><input type="checkbox" checked={staffRights.can_manage_staff} onChange={(event) => setStaffRights((value) => ({ ...value, can_manage_staff: event.target.checked }))} /><span>Добавлять сотрудников УК</span></label><label className="checkbox-row"><input type="checkbox" checked={staffRights.can_manage_residents} onChange={(event) => setStaffRights((value) => ({ ...value, can_manage_residents: event.target.checked }))} /><span>Управлять доступом жильцов</span></label><label className="checkbox-row"><input type="checkbox" checked={staffRights.can_manage_issues} onChange={(event) => setStaffRights((value) => ({ ...value, can_manage_issues: event.target.checked }))} /><span>Менять проблемы и отвечать на них</span></label></div>
      </>}
      {kind === "resident" && <>
        <AdminReferencePicker entity="houses" value={houseId} onChange={setHouseId} />
        <label className="field"><span>Квартира</span><input type="number" min="1" required value={apartment} onChange={(event) => setApartment(event.target.value)} /></label>
        <label className="field"><span>Доступ до (необязательно)</span><input type="datetime-local" value={validTo} onChange={(event) => setValidTo(event.target.value)} /></label>
        <p className="field-help">Жилец сам примет предложение после входа с подтверждённого номера.</p>
      </>}
      <button type="submit" className="button button--primary" disabled={busy || (kind === "staff" && !companyId) || (kind === "resident" && !houseId)}>{busy ? "Сохраняем…" : kind === "support" ? "Пригласить оператора" : kind === "staff" ? "Назначить сотрудника" : "Предложить доступ"}</button>
    </form>
  </details>;
}

function CompanyActions({ item, busy, onAction }: { item: Record<string, unknown>; busy: boolean; onAction: RunAction }) {
  const [name, setName] = useState(text(item, "display_name"));
  const [legalName, setLegalName] = useState(text(item, "legal_name"));
  const [inn, setInn] = useState(text(item, "inn"));
  const [ogrn, setOgrn] = useState(text(item, "ogrn"));
  return <details className="admin-action"><summary>Изменить данные УК</summary><form className="form-stack" onSubmit={(event) => { event.preventDefault(); void onAction("edit_company", { company_id: item.id, display_name: name.trim(), legal_name: optional(legalName), inn: optional(inn), ogrn: optional(ogrn) }, "Данные УК обновлены."); }}>
    <label className="field"><span>Название</span><input required value={name} onChange={(event) => setName(event.target.value)} /></label>
    <label className="field"><span>Юридическое название</span><input value={legalName} onChange={(event) => setLegalName(event.target.value)} /></label>
    <div className="field-grid"><label className="field"><span>ИНН</span><input value={inn} onChange={(event) => setInn(event.target.value)} inputMode="numeric" /></label><label className="field"><span>ОГРН</span><input value={ogrn} onChange={(event) => setOgrn(event.target.value)} inputMode="numeric" /></label></div>
    <button type="submit" className="button button--primary" disabled={busy || !name.trim()}>Сохранить УК</button>
  </form></details>;
}

function HouseActions({ item, busy, onAction }: { item: Record<string, unknown>; busy: boolean; onAction: RunAction }) {
  const [address, setAddress] = useState(text(item, "address_display"));
  const [entrances, setEntrances] = useState(text(item, "entrance_count"));
  const [apartments, setApartments] = useState(text(item, "apartment_count"));
  return <details className="admin-action"><summary>Изменить дом</summary><form className="form-stack" onSubmit={(event) => { event.preventDefault(); void onAction("edit_house", { house_id: item.id, address_display: address.trim(), entrance_count: Number(entrances), apartment_count: Number(apartments) }, "Данные дома обновлены."); }}>
    <label className="field"><span>Адрес</span><input required value={address} onChange={(event) => setAddress(event.target.value)} /></label>
    <div className="field-grid"><label className="field"><span>Количество подъездов</span><input required type="number" min="1" value={entrances} onChange={(event) => setEntrances(event.target.value)} /></label><label className="field"><span>Количество квартир</span><input required type="number" min="1" value={apartments} onChange={(event) => setApartments(event.target.value)} /></label></div>
    <button type="submit" className="button button--primary" disabled={busy || !address.trim() || !Number.isSafeInteger(Number(entrances)) || Number(entrances) < 1 || !Number.isSafeInteger(Number(apartments)) || Number(apartments) < 1}>Сохранить дом</button>
  </form></details>;
}

function IssueActions({ item, busy, categories, onAction }: { item: Record<string, unknown>; busy: boolean; categories: AdminIssueCategory[]; onAction: RunAction }) {
  const closed = item.status === "closed";
  const [title, setTitle] = useState(text(item, "title"));
  const [categoryId, setCategoryId] = useState(text(item, "category_id"));
  const [allHouse, setAllHouse] = useState(Boolean(item.scope_all_house));
  const targets = Array.isArray(item.targets) ? item.targets as Record<string, unknown>[] : [];
  const [entrances, setEntrances] = useState(targets.filter((value) => value.entrance_number !== null && value.entrance_number !== undefined).map((value) => String(value.entrance_number)).join(", "));
  const [apartments, setApartments] = useState(targets.filter((value) => value.apartment_number !== null && value.apartment_number !== undefined).map((value) => String(value.apartment_number)).join("\n"));
  const [scopeError, setScopeError] = useState("");
  const [status, setStatus] = useState(text(item, "status") || "open");
  const [note, setNote] = useState(text(item, "current_note"));
  const [closeResult, setCloseResult] = useState(text(item, "close_result") || "solved");
  const selectedCategoryMissing = categoryId && !categories.some((value) => value.id === categoryId);

  async function edit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setScopeError("");
    const entranceNumbers = entrances.trim() ? entrances.split(/[,\s]+/).map(Number) : [];
    const apartmentNumbers = apartments.trim() ? apartments.trim().split(/[\n,;]+/).map((part) => Number(part.trim())) : [];
    if (!allHouse && (entranceNumbers.some((value) => !Number.isInteger(value) || value < 1) || apartmentNumbers.some((value) => !Number.isInteger(value) || value < 1))) {
      setScopeError("Укажите номера подъездов через запятую, а номера квартир — по одному в строке."); return;
    }
    await onAction("edit_issue", { card_id: item.id, expected_version: integer(item, "version"), category_id: categoryId, title: title.trim(), scope_all_house: allHouse, target_entrances: allHouse ? [] : entranceNumbers, target_apartments: allHouse ? [] : apartmentNumbers.map((apartment_number) => ({ apartment_number })) }, "Карточка проблемы обновлена.");
  }

  return <>
    <details className="admin-action"><summary>Изменить карточку</summary><form className="form-stack" onSubmit={(event) => void edit(event)}>
      <label className="field"><span>Заголовок</span><input required value={title} onChange={(event) => setTitle(event.target.value)} /></label>
      <label className="field"><span>Категория</span><select required value={categoryId} onChange={(event) => setCategoryId(event.target.value)}>{selectedCategoryMissing && <option value={categoryId}>{categoryId}</option>}{categories.map((category) => <option key={category.id} value={category.id}>{category.name}</option>)}</select></label>
      <label className="checkbox-row"><input type="checkbox" checked={allHouse} onChange={(event) => setAllHouse(event.target.checked)} /><span>Затронут весь дом</span></label>
      {!allHouse && <><label className="field"><span>Подъезды через запятую</span><input value={entrances} onChange={(event) => setEntrances(event.target.value)} placeholder="1, 2" /></label><label className="field"><span>Квартиры, каждая отдельной строкой</span><textarea value={apartments} onChange={(event) => setApartments(event.target.value)} placeholder={"15\n42"} /></label></>}
      {scopeError && <p className="form-error" role="alert">{scopeError}</p>}
      <button type="submit" className="button button--primary" disabled={busy || !title.trim() || !categoryId}>Сохранить карточку</button>
    </form></details>
    <details className="admin-action"><summary>{closed ? "Изменить пояснение к закрытию" : "Изменить статус проблемы"}</summary><form className="form-stack" onSubmit={(event) => { event.preventDefault(); void onAction("set_issue_status", { card_id: item.id, status: closed ? "closed" : status, note: optional(note), close_result: closed || status === "closed" ? closeResult : null }, "Статус проблемы обновлён."); }}>
      {closed ? <p className="field-help">Закрытую проблему может переоткрыть только её автор. Здесь можно обновить пояснение и результат закрытия.</p> : <label className="field"><span>Статус</span><select value={status} onChange={(event) => setStatus(event.target.value)}><option value="open">Открыта</option><option value="reviewing">На рассмотрении</option><option value="needs_info">Нужны уточнения</option><option value="in_progress">В работе</option><option value="closed">Закрыта</option></select></label>}
      <label className="field"><span>Пояснение{status === "closed" ? " · обязательно" : ""}</span><textarea value={note} onChange={(event) => setNote(event.target.value)} placeholder="Что было сделано" /></label>
      {status === "closed" && <label className="field"><span>Результат</span><select value={closeResult} onChange={(event) => setCloseResult(event.target.value)}><option value="solved">Проблема решена</option><option value="invalid">Заявка некорректна</option></select></label>}
      <button type="submit" className="button button--primary" disabled={busy || (status === "closed" && !note.trim())}>Сохранить статус</button>
    </form></details>
  </>;
}

function RequestActions({ item, busy, onAction }: { item: Record<string, unknown>; busy: boolean; onAction: RunAction }) {
  const kind = text(item, "kind");
  const requestId = text(item, "request_id") || text(item, "id");
  const closed = item.status === "closed" || item.status === "cancelled";
  const [status, setStatus] = useState(text(item, "status") || "open");
  const [outcome, setOutcome] = useState(kind === "resident" ? "granted" : "approved");
  const [note, setNote] = useState("");
  const [displayName, setDisplayName] = useState(text(item, "proposed_company_name"));
  const [addressKey, setAddressKey] = useState("");
  const [entranceCount, setEntranceCount] = useState(text(item, "entrance_count"));
  const [apartmentCount, setApartmentCount] = useState(text(item, "apartment_count"));
  const [validTo, setValidTo] = useState("");
  const [message, setMessage] = useState("");
  if (!requestId || !["company_registration", "house_addition", "resident"].includes(kind)) return <p className="muted-text">Тип заявки не определён; действие недоступно.</p>;

  return <>
    {closed && <p className="muted-text">Заявка завершена. Её история остаётся доступной для просмотра.</p>}
    {!closed && <>
    <details className="admin-action"><summary>Рабочий статус заявки</summary><form className="form-stack" onSubmit={(event) => { event.preventDefault(); void onAction("request_status", { kind, request_id: requestId, status }, "Статус заявки обновлён."); }}>
      <label className="field"><span>Статус</span><select value={status} onChange={(event) => setStatus(event.target.value)}><option value="open">Открыта</option><option value="reviewing">На рассмотрении</option><option value="needs_info">Нужны уточнения</option></select></label>
      <button type="submit" className="button button--primary" disabled={busy || status === item.status}>Сохранить статус</button>
    </form></details>
    <details className="admin-action"><summary>Решение по заявке</summary><form className="form-stack" onSubmit={(event) => { event.preventDefault(); void onAction("request_decision", { kind, request_id: requestId, outcome, decision_note: note.trim(), ...(kind === "company_registration" ? { display_name: optional(displayName) } : {}), ...(kind === "house_addition" ? { proposed_address_key: optional(addressKey), entrance_count: outcome === "approved" ? Number(entranceCount) : null, apartment_count: outcome === "approved" ? Number(apartmentCount) : null } : {}), ...(kind === "resident" ? { valid_to: toIso(validTo) } : {}) }, "Решение по заявке сохранено."); }}>
      <label className="field"><span>Решение</span><select value={outcome} onChange={(event) => setOutcome(event.target.value)}>{kind === "resident" ? <><option value="granted">Выдать доступ</option><option value="denied">Отказать</option></> : <><option value="approved">Одобрить</option><option value="rejected">Отказать</option></>}</select></label>
      {kind === "company_registration" && outcome === "approved" && <label className="field"><span>Название УК</span><input required value={displayName} onChange={(event) => setDisplayName(event.target.value)} /></label>}
      {kind === "house_addition" && outcome === "approved" && <><label className="field"><span>Нормализованный адрес (необязательно)</span><input value={addressKey} onChange={(event) => setAddressKey(event.target.value)} placeholder="Если пусто, используется адрес заявки" /></label><div className="field-grid"><label className="field"><span>Количество подъездов</span><input required type="number" min="1" value={entranceCount} onChange={(event) => setEntranceCount(event.target.value)} /></label><label className="field"><span>Количество квартир</span><input required type="number" min="1" value={apartmentCount} onChange={(event) => setApartmentCount(event.target.value)} /></label></div></>}
      {kind === "resident" && outcome === "granted" && <label className="field"><span>Доступ до (необязательно)</span><input type="datetime-local" value={validTo} onChange={(event) => setValidTo(event.target.value)} /></label>}
      <label className="field"><span>Пояснение · обязательно</span><textarea required value={note} onChange={(event) => setNote(event.target.value)} placeholder="Почему принято это решение" /></label>
      <button type="submit" className="button button--primary" disabled={busy || !note.trim() || (kind === "company_registration" && outcome === "approved" && !displayName.trim()) || (kind === "house_addition" && outcome === "approved" && (!Number.isSafeInteger(Number(entranceCount)) || Number(entranceCount) < 1 || !Number.isSafeInteger(Number(apartmentCount)) || Number(apartmentCount) < 1))}>Сохранить решение</button>
    </form></details>
    </>}
    {item.status !== "cancelled" && <details className="admin-action"><summary>Написать в обсуждение заявки</summary><form className="form-stack" onSubmit={(event) => { event.preventDefault(); void onAction("add_access_message", { kind, request_id: requestId, text: message.trim() }, "Сообщение добавлено в обсуждение.").then((okay) => { if (okay) setMessage(""); }); }}><label className="field"><span>Текст сообщения</span><textarea required value={message} onChange={(event) => setMessage(event.target.value)} placeholder="Ответ от администратора проекта" /></label><button type="submit" className="button button--primary" disabled={busy || !message.trim()}>Отправить</button></form></details>}
  </>;
}

function RevokeAction({ label, confirmation, busy, action, payload, success, onAction }: { label: string; confirmation: string; busy: boolean; action: string; payload: Record<string, unknown>; success: string; onAction: RunAction }) {
  const [confirmed, setConfirmed] = useState(false);
  return <details className="admin-action admin-action--danger"><summary>{label}</summary><div className="form-stack"><p className="muted-text">{confirmation}</p><label className="checkbox-row"><input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} /><span>Подтверждаю это действие</span></label><button type="button" className="button button--soft" disabled={busy || !confirmed} onClick={() => void onAction(action, payload, success).then((okay) => { if (okay) setConfirmed(false); })}>{label}</button></div></details>;
}

function UserNameAction({ item, busy, onAction }: { item: Record<string, unknown>; busy: boolean; onAction: RunAction }) {
  const [fullName, setFullName] = useState(text(item, "full_name"));
  const hasMaxName = Boolean(text(item, "max_display_name") || text(item, "max_username"));
  return <div className="admin-action">
    <strong>Имя в списке людей</strong>
    <p className="field-help">Можно указать имя или псевдоним. Номер телефона останется под ним и не изменится.</p>
    <form className="form-stack" onSubmit={(event) => { event.preventDefault(); void onAction("edit_user_name", { user_id: item.id, full_name: optional(fullName) }, "Имя пользователя обновлено."); }}>
      <label className="field"><span>Имя для отображения</span><input maxLength={255} value={fullName} onChange={(event) => setFullName(event.target.value)} placeholder="Как показывать этого человека" /></label>
      <div className="button-row">
        <button type="submit" className="button button--primary" disabled={busy || fullName.trim() === text(item, "full_name").trim()}>Сохранить имя</button>
        {item.full_name_is_manual === true && <button type="button" className="button button--soft" disabled={busy} onClick={() => void onAction("edit_user_name", { user_id: item.id, full_name: null }, "Ручное имя сброшено.")}>{hasMaxName ? "Использовать имя из MAX" : "Сбросить ручное имя"}</button>}
      </div>
    </form>
  </div>;
}

function FileAction({ item }: { item: Record<string, unknown> }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function download() {
    setBusy(true); setError("");
    try { await downloadAdminFile(text(item, "id"), text(item, "original_name") || "вложение"); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Не удалось скачать файл"); }
    finally { setBusy(false); }
  }
  const available = item.state === "ready" || item.state === "staged";
  return <div className="admin-actions"><h3>Действия</h3><button type="button" className="button button--soft" disabled={busy || !available} onClick={() => void download()}>Скачать файл</button>{!available && <p className="field-help">Файл недоступен для скачивания.</p>}{error && <p className="form-error" role="alert">{error}</p>}</div>;
}

function GrantRevokeAction({ id, busy, onAction }: { id: string; busy: boolean; onAction: RunAction }) {
  const [reason, setReason] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  return <details className="admin-action admin-action--danger"><summary>Отозвать доступ жильца</summary><div className="form-stack"><p className="muted-text">Жилец потеряет доступ к дому. История доступа сохранится.</p><label className="field"><span>Причина</span><textarea required value={reason} onChange={(event) => setReason(event.target.value)} placeholder="Почему доступ отзывается" /></label><label className="checkbox-row"><input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} /><span>Подтверждаю отзыв доступа</span></label><button type="button" className="button button--soft" disabled={busy || !confirmed || !reason.trim()} onClick={() => void onAction("revoke_grant", { grant_id: id, reason: reason.trim() }, "Доступ жильца отозван.").then((okay) => { if (okay) { setReason(""); setConfirmed(false); } })}>Отозвать доступ</button></div></details>;
}

export function AdminItemActions({ entity, item, busy, categories, onAction, onOpenIssue }: { entity: AdminEntity; item: Record<string, unknown>; busy: boolean; categories: AdminIssueCategory[]; onAction: RunAction; onOpenIssue: (id: string) => void }) {
  const id = text(item, "id");
  if (entity === "users") return <div className="admin-actions"><h3>Действия</h3><UserNameAction item={item} busy={busy} onAction={onAction} />{item.kind === "support" && <RevokeAction label="Отозвать права оператора" confirmation="Оператор больше не сможет разбирать обращения. Его аккаунт и история действий сохранятся." busy={busy} action="revoke_support" payload={{ target_user_id: id }} success="Права оператора отозваны." onAction={onAction} />}</div>;
  if (entity === "companies") return <div className="admin-actions"><h3>Действия</h3><CompanyActions item={item} busy={busy} onAction={onAction} /></div>;
  if (entity === "houses") return <div className="admin-actions"><h3>Действия</h3><HouseActions item={item} busy={busy} onAction={onAction} /></div>;
  if (entity === "issues") {
    const primaryId = text(item, "merged_into_id");
    if (primaryId) return <div className="admin-actions"><h3>Объединённая проблема</h3><p className="muted-text">Эта карточка объединена с основной. Изменения здесь недоступны.</p><p className="field-help">Основная карточка: {primaryId}</p><button type="button" className="button button--soft" onClick={() => onOpenIssue(primaryId)}>Открыть основную карточку</button></div>;
    return <div className="admin-actions"><h3>Действия</h3><IssueActions item={item} busy={busy} categories={categories} onAction={onAction} /></div>;
  }
  if (entity === "access_requests") return <div className="admin-actions"><h3>Действия</h3><RequestActions item={item} busy={busy} onAction={onAction} /></div>;
  if (entity === "staff" && !item.revoked_at) return <div className="admin-actions"><h3>Действия</h3><RevokeAction label="Отозвать права сотрудника" confirmation="Сотрудник потеряет доступ к этой УК. История назначения сохранится." busy={busy} action="revoke_staff" payload={{ assignment_id: id }} success="Права сотрудника отозваны." onAction={onAction} /></div>;
  if (entity === "resident_grants" && !item.revoked_at && item.status !== "revoked") return <div className="admin-actions"><h3>Действия</h3><GrantRevokeAction id={id} busy={busy} onAction={onAction} /></div>;
  if (entity === "support_invites" && !item.revoked_at && !item.accepted_at) return <div className="admin-actions"><h3>Действия</h3><RevokeAction label="Отозвать приглашение" confirmation="Приглашённый человек больше не сможет принять это приглашение. История сохранится." busy={busy} action="revoke_support_invite" payload={{ invitation_id: id }} success="Приглашение отозвано." onAction={onAction} /></div>;
  if (entity === "files") return <FileAction item={item} />;
  return null;
}
