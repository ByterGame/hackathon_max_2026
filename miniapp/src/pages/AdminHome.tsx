import { useCallback, useEffect, useState } from "react";

import {
  getAdminItem,
  getAdminIssueCategories,
  getAdminOverview,
  listAdminItems,
  runAdminAction,
  type AdminEntity,
  type AdminListItem,
  type AdminOverview,
  type AdminPage,
  type AdminIssueCategory,
} from "../features/admin/integrations/client_api";
import { Icon } from "../shared/common_ui/Icon";
import { ScreenHeader } from "../shared/common_ui/ScreenHeader";
import { formatCount } from "../shared/format_count";
import { AdminAddAccess, AdminItemActions } from "./admin/AdminActions";
import { SystemEditor } from "./admin/SystemEditor";

const sections: { entity: AdminEntity; label: string; icon: "user" | "building" | "home" | "pin" | "list" | "chat" | "people" | "key" | "check" | "shield" | "paperclip" | "clock" }[] = [
  { entity: "users", label: "Люди", icon: "user" },
  { entity: "companies", label: "УК", icon: "building" },
  { entity: "houses", label: "Дома", icon: "home" },
  { entity: "apartments", label: "Квартиры", icon: "pin" },
  { entity: "issues", label: "Проблемы", icon: "list" },
  { entity: "access_requests", label: "Заявки", icon: "chat" },
  { entity: "staff", label: "Сотрудники", icon: "people" },
  { entity: "resident_grants", label: "Доступы жильцов", icon: "key" },
  { entity: "offers", label: "Приглашения жильцов", icon: "check" },
  { entity: "support_invites", label: "Приглашения операторов", icon: "shield" },
  { entity: "files", label: "Файлы", icon: "paperclip" },
  { entity: "audit", label: "История действий", icon: "clock" },
];

const labels: Record<string, string> = {
  id: "Идентификатор", request_id: "Номер заявки", kind: "Тип", max_user_id: "MAX ID", phone_number: "Номер телефона",
  full_name: "Имя для отображения", max_display_name: "Имя в MAX", max_username: "Ник в MAX", full_name_is_manual: "Имя задано вручную", phone_verified_at: "Номер подтверждён", company_id: "УК", house_id: "Дом", applicant_user_id: "Заявитель",
  author_user_id: "Автор", user_id: "Пользователь", display_name: "Название", legal_name: "Юридическое название", inn: "ИНН", ogrn: "ОГРН",
  address_display: "Адрес", entrance_count: "Количество подъездов", apartment_count: "Количество квартир", entrance_number: "Подъезд", apartment_number: "Квартира",
  status: "Состояние", outcome: "Решение", decision_note: "Пояснение", title: "Заголовок", category_id: "Категория",
  scope_all_house: "Весь дом", current_note: "Пояснение УК", close_result: "Результат закрытия", support_count: "Поддержали",
  proposed_company_name: "Название УК", entered_address: "Адрес", submitted_full_name: "ФИО", submitted_entrance_number: "Подъезд",
  submitted_apartment_number: "Квартира", free_text: "Описание", discussion: "Обсуждение", reports: "Сообщения жильцов",
  messages: "Сообщения", history: "История", targets: "Затронутые места", created_at: "Создано", updated_at: "Обновлено",
  revoked_at: "Отозвано", archived_at: "Архивировано", valid_to: "Срок доступа", can_manage_staff: "Управляет сотрудниками",
  can_manage_residents: "Управляет жильцами", can_manage_issues: "Управляет проблемами", version: "Версия",
  accepted_at: "Принято", invited_by: "Пригласил", accepted_by: "Принял", revoked_by: "Отозвал", original_name: "Имя файла",
  mime_type: "Тип файла", byte_size: "Размер, байт", state: "Состояние файла", apartment_id: "Квартира", action: "Действие",
  entity_kind: "Тип записи", entity_id: "Запись", actor_user_id: "Инициатор", before_data: "Было", after_data: "Стало",
};

function valueText(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "boolean") return value ? "Да" : "Нет";
  if (typeof value === "object") return JSON.stringify(value, null, 2);
  if (typeof value === "string" && /^\d{4}-\d{2}-\d{2}T/.test(value)) {
    const date = new Date(value);
    if (!Number.isNaN(date.getTime())) return date.toLocaleString("ru-RU");
  }
  return String(value);
}

const statusLabels: Record<string, string> = {
  unassigned: "Без роли", resident: "Жилец", employee: "Сотрудник УК", support: "Оператор", admin: "Администратор",
  active: "Действует", archived: "В архиве", revoked: "Отозвано", pending: "Ожидает", accepted: "Принято", merged: "Объединена",
  open: "Открыта", reviewing: "На рассмотрении", needs_info: "Нужны уточнения", in_progress: "В работе", closed: "Закрыта", cancelled: "Отменена",
  company_registration: "Регистрация УК", house_addition: "Подключение дома", resident_request: "Доступ жильца",
};

function localized(value: string): string { return statusLabels[value] ?? value; }

function localizedSubtitle(entity: AdminEntity, value: string): string {
  return entity === "access_requests" && value === "resident" ? "Доступ жильца" : localized(value);
}

function userTitle(item: AdminListItem): string {
  const name = item.data?.full_name;
  if (typeof name === "string" && name.trim()) return name.trim();
  if (item.data && "full_name" in item.data) return "Имя не указано";
  const title = item.title?.trim();
  const phone = item.data?.phone_number ?? item.subtitle;
  const maxId = item.data?.max_user_id;
  return title && title !== phone && title !== maxId && title !== item.id ? title : "Имя не указано";
}

function userContact(item: AdminListItem): string | null {
  const phone = item.data?.phone_number ?? item.subtitle;
  const maxId = item.data?.max_user_id;
  const identity = typeof phone === "string" && phone.trim() ? phone : typeof maxId === "string" && maxId.trim() ? `MAX ID: ${maxId}` : null;
  const username = item.data?.max_username;
  const nick = typeof username === "string" && username.trim() ? `@${username.replace(/^@/, "")}` : null;
  const displayName = item.data?.full_name;
  return [identity, nick && displayName !== nick ? nick : null].filter(Boolean).join(" · ") || null;
}

function AdminRecordLabel({ entity, item }: { entity: AdminEntity; item: AdminListItem }) {
  if (entity === "users") {
    const contact = userContact(item);
    return <span className="admin-record__main"><strong>{userTitle(item)}</strong>{contact && <small>{contact}</small>}</span>;
  }
  return <span className="admin-record__main"><strong>{item.title || item.id}</strong>{item.subtitle && <small>{localizedSubtitle(entity, item.subtitle)}</small>}</span>;
}

function AdminDetails({ item }: { item: Record<string, unknown> }) {
  return <dl className="admin-details">
    {Object.entries(item).map(([key, value]) => <div key={key} className="admin-details__row">
      <dt>{labels[key] ?? key.replaceAll("_", " ")}</dt>
      <dd className={typeof value === "object" && value !== null ? "admin-details__structured" : ""}>{valueText(value)}</dd>
    </div>)}
  </dl>;
}

export function AdminHome({ currentUserId, onOwnAccountChanged }: { currentUserId: string; onOwnAccountChanged: () => void }) {
  const [systemEditorOpen, setSystemEditorOpen] = useState(false);
  const [entity, setEntity] = useState<AdminEntity>("users");
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [requestKind, setRequestKind] = useState("");
  const [offset, setOffset] = useState(0);
  const [refreshKey, setRefreshKey] = useState(0);
  const [overview, setOverview] = useState<AdminOverview | null>(null);
  const [page, setPage] = useState<AdminPage | null>(null);
  const [selected, setSelected] = useState<AdminListItem | null>(null);
  const [detail, setDetail] = useState<Record<string, unknown> | null>(null);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [categories, setCategories] = useState<AdminIssueCategory[]>([]);

  const refresh = useCallback(() => setRefreshKey((current) => current + 1), []);

  useEffect(() => {
    let cancelled = false;
    void Promise.all([getAdminOverview(), getAdminIssueCategories()])
      .then(([nextOverview, categoryList]) => {
        if (cancelled) return;
        setOverview(nextOverview); setCategories(categoryList);
      })
      .catch((reason) => { if (!cancelled) setError(reason instanceof Error ? reason.message : "Не удалось загрузить сводку"); });
    return () => { cancelled = true; };
  }, [refreshKey]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true); setError("");
    void listAdminItems(entity, query, offset, 20, requestKind)
      .then((result) => { if (!cancelled) setPage(result); })
      .catch((reason) => { if (!cancelled) setError(reason instanceof Error ? reason.message : "Не удалось загрузить список"); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [entity, query, requestKind, offset, refreshKey]);

  useEffect(() => {
    if (!selected) { setDetail(null); return; }
    let cancelled = false;
    setDetailLoading(true); setDetail(null);
    void getAdminItem(entity, selected.id)
      .then((item) => { if (!cancelled) setDetail(item); })
      .catch((reason) => { if (!cancelled) setError(reason instanceof Error ? reason.message : "Не удалось открыть запись"); })
      .finally(() => { if (!cancelled) setDetailLoading(false); });
    return () => { cancelled = true; };
  }, [entity, selected, refreshKey]);

  useEffect(() => {
    if (!selected) return;
    const frame = requestAnimationFrame(() => document.getElementById("admin-detail")?.scrollIntoView({ behavior: "smooth", block: "start" }));
    return () => cancelAnimationFrame(frame);
  }, [selected]);

  async function action(name: string, payload: Record<string, unknown>, success: string): Promise<boolean> {
    setBusy(true); setError(""); setNotice("");
    try {
      await runAdminAction(name, payload);
      setNotice(success);
      refresh();
      if (payload.user_id === currentUserId || payload.target_user_id === currentUserId) onOwnAccountChanged();
      return true;
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Изменение не сохранено"); return false; }
    finally { setBusy(false); }
  }

  function switchEntity(next: AdminEntity) {
    setEntity(next); setSearch(""); setQuery(""); setRequestKind(""); setOffset(0); setSelected(null); setNotice(""); setError("");
    window.scrollTo({ top: 0, behavior: "auto" });
  }

  const current = sections.find((item) => item.entity === entity)!;
  const pageNumber = page ? Math.floor(offset / page.limit) + 1 : 1;
  const totalPages = page ? Math.max(1, Math.ceil(page.total / page.limit)) : 1;

  if (systemEditorOpen) return <SystemEditor onExit={() => setSystemEditorOpen(false)} currentUserId={currentUserId} onOwnAccountChanged={onOwnAccountChanged} />;

  return <div className="page page--admin">
    <ScreenHeader title="Администрирование" subtitle="Полный обзор сервиса" icon="shield" />
    <div className="info-panel admin-intro"><Icon name="shield" size={20} /> Это отдельный кабинет администратора. Поддержка видит только обращения, переданные ей в работу.</div>
    <div className="panel system-entry"><div><strong>Системный редактор</strong><p>Для исключительных правок записей, включая идентификаторы и аудит. Требует причины и проверки версии.</p></div><button type="button" className="button button--soft" onClick={() => setSystemEditorOpen(true)}>Открыть системные операции</button></div>
    {overview && <div className="admin-overview" aria-label="Сводка по сервису">
      {sections.filter((section) => ["users", "companies", "houses", "issues", "access_requests"].includes(section.entity)).map((section) => <button type="button" key={section.entity} className="panel admin-overview__tile" onClick={() => switchEntity(section.entity)}>
        <Icon name={section.icon} size={20} /><span>{section.label}</span><strong>{overview.counts[section.entity] ?? 0}</strong>
      </button>)}
    </div>}
    <AdminAddAccess busy={busy} onAction={action} />
    <nav className="admin-sections" aria-label="Разделы администратора">
      {sections.map((section) => <button key={section.entity} type="button" className={entity === section.entity ? "is-active" : ""} onClick={() => switchEntity(section.entity)}><Icon name={section.icon} size={17} />{section.label}</button>)}
    </nav>
    <section className="panel admin-list-panel">
      <div className="section-heading"><div><h2>{current.label}</h2><p>{page ? formatCount(page.total, ["запись", "записи", "записей"]) : "Загружаем…"}</p></div><button type="button" className="button button--soft" onClick={refresh} disabled={loading || busy}>Обновить</button></div>
      <form className="admin-search" onSubmit={(event) => { event.preventDefault(); setSelected(null); setOffset(0); setQuery(search.trim()); }}>
        <label className="search-field"><Icon name="search" size={19} /><input type="search" maxLength={100} value={search} onChange={(event) => setSearch(event.target.value)} placeholder={entity === "users" ? "Имя, ник, телефон или MAX ID" : "Поиск по разделу"} aria-label={`Поиск: ${current.label}`} /></label>
        {entity === "access_requests" && <label className="admin-kind-filter"><span className="sr-only">Тип заявки</span><select value={requestKind} onChange={(event) => { setRequestKind(event.target.value); setOffset(0); setSelected(null); }}><option value="">Все виды</option><option value="resident">Доступ жильца</option><option value="company_registration">Регистрация УК</option><option value="house_addition">Подключение дома</option></select></label>}
        <button type="submit" className="button button--primary">Найти</button>
      </form>
      {error && <p className="form-error" role="alert">{error}</p>}
      {notice && <p className="form-success" role="status">{notice}</p>}
      {loading && <p className="muted-text">Загружаем записи…</p>}
      {!loading && page?.items.length === 0 && <p className="muted-text">Записи не найдены.</p>}
      {!loading && page && <div className="admin-records">
        {page.items.map((item) => <button key={item.id} type="button" className={`admin-record${selected?.id === item.id ? " admin-record--active" : ""}`} onClick={() => setSelected((currentItem) => currentItem?.id === item.id ? null : item)}>
          <AdminRecordLabel entity={entity} item={item} />
          <span className="admin-record__meta">{item.status && <em>{localized(item.status)}</em>}{item.updated_at && <small>{valueText(item.updated_at)}</small>}</span>
          <Icon name="chevron" size={18} />
        </button>)}
      </div>}
      {page && page.total > page.limit && <div className="admin-pagination"><button type="button" className="button button--soft" disabled={offset === 0 || loading} onClick={() => { setSelected(null); setOffset(Math.max(0, offset - page.limit)); }}>Назад</button><span>Страница {pageNumber} из {totalPages}</span><button type="button" className="button button--soft" disabled={offset + page.limit >= page.total || loading} onClick={() => { setSelected(null); setOffset(offset + page.limit); }}>Далее</button></div>}
    </section>
    {selected && <section className="panel admin-detail-panel" id="admin-detail">
      <div className="section-heading"><div><h2>{entity === "users" ? userTitle(detail ? { ...selected, data: detail } : selected) : selected.title || "Запись"}</h2><p>{entity === "users" ? userContact(detail ? { ...selected, data: detail } : selected) : selected.subtitle}</p></div><button type="button" className="icon-button icon-button--soft" aria-label="Закрыть запись" onClick={() => setSelected(null)}><Icon name="close" size={18} /></button></div>
      {detailLoading && <p className="muted-text">Загружаем подробности…</p>}
      {detail && <><AdminDetails item={detail} /><AdminItemActions key={`${entity}:${selected.id}:${refreshKey}:${detail.version ?? detail.updated_at ?? ""}`} entity={entity} item={detail} busy={busy} categories={categories} onAction={action} onOpenIssue={(id) => setSelected(page?.items.find((item) => item.id === id) ?? { id, title: `Карточка ${id}` })} /></>}
    </section>}
  </div>;
}
