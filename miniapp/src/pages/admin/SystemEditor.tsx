import { useEffect, useState, type FormEvent } from "react";

import {
  deleteAdminSystemRow,
  getAdminSystemRow,
  getAdminSystemSchema,
  listAdminSystemRows,
  listAdminSystemOperations,
  patchAdminSystemRow,
  retryAdminSystemFileCleanup,
  type AdminSystemDetail,
  type AdminSystemCascadePreview,
  type AdminSystemEntity,
  type AdminSystemField,
  type AdminSystemPage,
  type AdminSystemOperationsPage,
  type AdminSystemRow,
} from "../../features/admin/integrations/client_api";
import { Icon } from "../../shared/common_ui/Icon";
import { ScreenHeader } from "../../shared/common_ui/ScreenHeader";

const rowTitleFields = ["display_name", "full_name", "title", "address_display", "phone_number", "name", "code", "action", "status"];
const userKindOptions = [
  { value: "unassigned", label: "Без роли" },
  { value: "resident", label: "Жилец" },
  { value: "employee", label: "Сотрудник УК" },
  { value: "support", label: "Оператор" },
  { value: "admin", label: "Администратор" },
];

function displayValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "object") return JSON.stringify(value, null, 2);
  if (typeof value === "boolean") return value ? "Да" : "Нет";
  return String(value);
}

function rowTitle(row: AdminSystemRow): string {
  for (const field of rowTitleFields) {
    const value = row.data[field];
    if (value !== null && value !== undefined && value !== "") return String(value);
  }
  return row.id;
}

function localDateTime(value: unknown): string {
  if (!value) return "";
  const date = new Date(String(value));
  if (Number.isNaN(date.getTime())) return String(value);
  return new Date(date.getTime() - date.getTimezoneOffset() * 60_000).toISOString().slice(0, 19);
}

function editorValue(field: AdminSystemField, value: unknown): string {
  if (value === null || value === undefined) return "";
  if (field.type === "bool") return value ? "true" : "false";
  if (field.type === "json") return JSON.stringify(value, null, 2);
  if (field.type === "datetime") return localDateTime(value);
  return String(value);
}

function parsedValue(field: AdminSystemField, value: string, isNull: boolean): unknown {
  if (isNull) {
    if (!field.nullable) throw new Error(`Поле «${field.name}» не может быть пустым`);
    return null;
  }
  if (field.type === "bool") {
    if (value === "true") return true;
    if (value === "false") return false;
    if (field.nullable) return null;
    throw new Error(`Укажите значение поля «${field.name}»`);
  }
  if (field.type === "int") {
    if (!/^-?\d+$/.test(value.trim())) throw new Error(`Поле «${field.name}» должно быть целым числом`);
    const number = Number(value);
    if (!Number.isSafeInteger(number)) throw new Error(`Число в поле «${field.name}» выходит за безопасный диапазон`);
    return number;
  }
  if (field.type === "datetime") {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) throw new Error(`Укажите корректные дату и время в поле «${field.name}»`);
    return date.toISOString();
  }
  if (field.type === "json") {
    try { return JSON.parse(value) as unknown; }
    catch { throw new Error(`Поле «${field.name}» должно содержать корректный JSON`); }
  }
  if (field.type === "uuid" && value.trim() === "") throw new Error(`Укажите идентификатор в поле «${field.name}»`);
  return field.type === "uuid" ? value.trim() : value;
}

function CascadePreview({ preview }: { preview: AdminSystemCascadePreview }) {
  return <div className="system-dependencies">
    <strong>Точный состав каскада: {preview.total} записей</strong>
    <p className="field-help">Хеш снимка: <code className="system-record__id">{preview.hash}</code>. Если связанные данные изменятся до подтверждения, сервер отклонит удаление.</p>
    <ul>{preview.counts.map(({ entity, count }) => <li key={entity}>{entity}: {count}</li>)}</ul>
    {preview.blockers.length > 0 && <div className="form-error" role="alert"><strong>Удаление заблокировано:</strong><ul>{preview.blockers.map((blocker, index) => <li key={`${index}:${blocker}`}>{blocker}</li>)}</ul></div>}
    {preview.updates.length > 0 && <details open><summary>Связанные записи, которые будут изменены: {preview.updates.length}</summary><ul>{preview.updates.map((update, index) => <li key={`${update.entity}:${update.id}:${update.field}:${index}`}><strong>{update.entity} · {update.id} · {update.field}</strong>: {displayValue(update.before)} → {displayValue(update.after)}</li>)}</ul></details>}
    {preview.files.length > 0 && <details open><summary>Файлы, которые будут удалены: {preview.files.length}</summary><ul>{preview.files.map((file) => <li key={file.id}>{file.original_name} · {file.size_bytes.toLocaleString("ru-RU")} байт · {file.id}</li>)}</ul></details>}
    <details open><summary>Все удаляемые записи: {preview.rows.length}</summary><ol>{preview.rows.map((row) => <li key={`${row.entity}:${row.id}`}><strong>{row.entity}</strong> · <code className="system-record__id">{row.id}</code>{row.via.length === 0 ? " · выбранная запись" : <ul>{row.via.map((edge, index) => <li key={`${edge.kind}:${edge.from_entity}:${edge.from_id}:${edge.field}:${index}`}>{edge.kind} · {edge.from_entity} · {edge.from_id} · {edge.field}</li>)}</ul>}</li>)}</ol></details>
  </div>;
}

function SystemRecordEditor({ entity, detail, onRefresh, onMutated, currentUserId, onOwnAccountChanged }: {
  entity: AdminSystemEntity;
  detail: AdminSystemDetail;
  onRefresh: () => void;
  onMutated: (message: string, hardDeleted: boolean, warning?: boolean) => void;
  currentUserId: string;
  onOwnAccountChanged: () => void;
}) {
  const { item, dependencies } = detail;
  const editableFields = entity.fields.filter((field) => field.editable);
  const [draft, setDraft] = useState<Record<string, string>>(() => Object.fromEntries(editableFields.map((field) => [field.name, editorValue(field, item.data[field.name])])));
  const [nullFields, setNullFields] = useState<Record<string, boolean>>(() => Object.fromEntries(editableFields.map((field) => [field.name, item.data[field.name] === null])));
  const [dirty, setDirty] = useState<string[]>([]);
  const [patchReason, setPatchReason] = useState("");
  const [deleteReason, setDeleteReason] = useState("");
  const [deleteMode, setDeleteMode] = useState<"soft" | "hard">(detail.delete_mode);
  const [confirmed, setConfirmed] = useState(false);
  const [confirmCount, setConfirmCount] = useState("");
  const [confirmHash, setConfirmHash] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const deleteModes = detail.delete_modes;
  const dependencyCount = dependencies.reduce((sum, dependency) => sum + dependency.count, 0);
  const preview = detail.cascade_preview;
  const storedCleanup = item.data.after_data && typeof item.data.after_data === "object"
    ? (item.data.after_data as Record<string, unknown>)._file_cleanup
    : null;
  const pendingCleanup = entity.key === "system.admin_operations" && Boolean(storedCleanup) && typeof storedCleanup === "object"
    && Array.isArray((storedCleanup as Record<string, unknown>).pending)
    && ((storedCleanup as { pending: unknown[] }).pending.length > 0);
  const deleteReadinessIssues: string[] = [];
  if (pendingCleanup) deleteReadinessIssues.push("Сначала завершите очистку файлов через кнопку повтора в журнале операций; до этого запись защищена от правки и удаления.");
  if (!deleteModes.includes(deleteMode)) deleteReadinessIssues.push("Сервер не разрешает выбранный способ удаления.");
  if (deleteMode === "hard") {
    if (!preview) deleteReadinessIssues.push("Сервер не предоставил предпросмотр каскада. Обновите запись; без него удаление невозможно.");
    else {
      if (!preview.hash || !Number.isSafeInteger(preview.total) || preview.total < 1 || preview.rows.length !== preview.total || preview.counts.reduce((sum, count) => sum + count.count, 0) !== preview.total) deleteReadinessIssues.push("Предпросмотр каскада неполный или некорректный. Обновите запись.");
      if (preview.blockers.length > 0) deleteReadinessIssues.push("Устраните перечисленные выше блокировки на сервере.");
      if (confirmCount.trim() !== String(preview.total)) deleteReadinessIssues.push(`Введите количество удаляемых записей: ${preview.total}.`);
      if (confirmHash.trim() !== preview.hash) deleteReadinessIssues.push("Введите полный хеш снимка без изменений.");
    }
  }
  if (deleteReason.trim().length < 5) deleteReadinessIssues.push("Укажите причину длиной не менее 5 символов.");
  if (!confirmed) deleteReadinessIssues.push("Отметьте подтверждение операции.");
  const changingPhone = entity.key === "identity.users" && dirty.includes("phone_number");
  const changingMaxId = entity.key === "identity.users" && dirty.includes("max_user_id");

  function markDirty(name: string) {
    setDirty((current) => current.includes(name) ? current : [...current, name]);
  }

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setError("");
    if (patchReason.trim().length < 5) { setError("Причина должна содержать не менее 5 символов"); return; }
    const changes: Record<string, unknown> = {};
    try {
      for (const name of dirty) {
        const field = editableFields.find((candidate) => candidate.name === name);
        if (field) changes[name] = parsedValue(field, draft[name] ?? "", Boolean(nullFields[name]));
      }
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Проверьте значения полей"); return; }
    if (!Object.keys(changes).length) { setError("Измените хотя бы одно поле"); return; }
    setBusy(true);
    try {
      const result = await patchAdminSystemRow(entity.key, item.id, item.etag, patchReason.trim(), changes);
      onMutated(`Изменения сохранены. Операция ${result.operation_id}.`, false);
      if (entity.key === "identity.users" && item.id === currentUserId) onOwnAccountChanged();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Не удалось сохранить запись"); }
    finally { setBusy(false); }
  }

  async function remove(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setError("");
    if (deleteReason.trim().length < 5) { setError("Причина должна содержать не менее 5 символов"); return; }
    if (!confirmed) { setError("Подтвердите действие"); return; }
    if (!deleteModes.includes(deleteMode)) { setError("Такой способ удаления недоступен для этой модели"); return; }
    if (deleteReadinessIssues.length > 0) { setError(deleteReadinessIssues[0]); return; }
    setBusy(true);
    try {
      const result = await deleteAdminSystemRow(entity.key, item.id, item.etag, deleteReason.trim(), deleteMode, deleteMode === "hard" ? preview?.hash : undefined);
      let message = `${result.mode === "soft" ? "Запись архивирована" : "Записи из предпросмотра удалены"}. Операция ${result.operation_id}.`;
      let warning = false;
      if (result.mode === "hard") {
        const cleanup = result.file_cleanup;
        if (!cleanup) { message += " Сервер не сообщил результат очистки файлов."; warning = true; }
        else if (cleanup.status === "done") message += ` Файлов очищено: ${cleanup.deleted}.`;
        else if (cleanup.status === "pending") { message += ` Очистка файлов ожидает выполнения: удалено ${cleanup.deleted}, ошибок ${cleanup.failed}.`; warning = true; }
        else { message += ` Очистка файлов завершилась с ошибками: удалено ${cleanup.deleted}, ошибок ${cleanup.failed}.`; warning = true; }
      }
      onMutated(message, result.mode === "hard", warning);
      const ownAccountAffected = (entity.key === "identity.users" && item.id === currentUserId)
        || (result.mode === "hard" && Boolean(preview?.rows.some((row) => row.entity === "identity.users" && row.id === currentUserId)
          || preview?.updates.some((update) => update.entity === "identity.users" && update.id === currentUserId)));
      if (ownAccountAffected) onOwnAccountChanged();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Не удалось выполнить действие"); }
    finally { setBusy(false); }
  }

  return <div className="system-record">
    <div className="section-heading"><div><h2>{rowTitle(item)}</h2><p className="system-record__id">{item.id}</p></div><button type="button" className="button button--soft" onClick={onRefresh} disabled={busy}>Обновить запись и связи</button></div>
    <p className="field-help">Версия снимка: <code>{item.etag.slice(0, 16)}…</code>. При изменении записи другим человеком сервер отклонит сохранение; обновите запись и повторите правку. Обновление сбросит несохранённые поля.</p>
    <details className="admin-action"><summary>Все поля записи</summary><dl className="admin-details">{Object.entries(item.data).map(([name, value]) => <div className="admin-details__row" key={name}><dt>{name}</dt><dd className={typeof value === "object" && value !== null ? "admin-details__structured" : ""}>{displayValue(value)}</dd></div>)}</dl></details>
    <section className="system-operation system-operation--edit">
      <h3>Изменить поля</h3><p className="muted-text">Только поля, разрешённые серверным реестром. Пустое значение и NULL различаются.</p>
      {pendingCleanup && <p className="form-error" role="alert">По этой операции ещё не завершено удаление байтов файлов. Повторите очистку в журнале выше; до этого править запись нельзя.</p>}
      {editableFields.length === 0 ? <p className="muted-text">У этой модели нет редактируемых полей.</p> : <form className="form-stack" onSubmit={(event) => void save(event)}>
        {editableFields.map((field) => <div className="system-field" key={field.name}>
          {field.clear_only ? <><p className="field-help">{field.name}: {displayValue(item.data[field.name])}. Это поле можно только очистить; установить новое значение можно лишь через штатный процесс.</p><label className="checkbox-row"><input type="checkbox" disabled={item.data[field.name] === null} checked={dirty.includes(field.name) && Boolean(nullFields[field.name])} onChange={(event) => { setNullFields((current) => ({ ...current, [field.name]: event.target.checked })); setDirty((current) => event.target.checked ? current.includes(field.name) ? current : [...current, field.name] : current.filter((name) => name !== field.name)); }} /><span>Очистить поле</span></label></> : <>
          <label className="field"><span>{field.name} <small>· {field.type}{field.foreign_key ? ` · связь с ${field.foreign_key}` : ""}</small></span>
            {entity.key === "identity.users" && field.name === "kind" ? <select value={draft[field.name] ?? ""} onChange={(event) => { setDraft((current) => ({ ...current, [field.name]: event.target.value })); markDirty(field.name); }}>{userKindOptions.map((option) => <option key={option.value} value={option.value}>{option.label} ({option.value})</option>)}</select>
              : field.type === "bool" ? <select value={nullFields[field.name] ? "" : draft[field.name]} onChange={(event) => { setDraft((current) => ({ ...current, [field.name]: event.target.value })); setNullFields((current) => ({ ...current, [field.name]: event.target.value === "" })); markDirty(field.name); }}><option value="" disabled={!field.nullable}>Не задано (NULL)</option><option value="true">Да</option><option value="false">Нет</option></select>
              : field.type === "json" ? <textarea value={draft[field.name] ?? ""} disabled={Boolean(nullFields[field.name])} onChange={(event) => { setDraft((current) => ({ ...current, [field.name]: event.target.value })); markDirty(field.name); }} rows={5} spellCheck={false} />
                : <input type={field.type === "int" ? "number" : field.type === "datetime" ? "datetime-local" : "text"} step={field.type === "datetime" ? "1" : field.type === "int" ? "1" : undefined} value={draft[field.name] ?? ""} disabled={Boolean(nullFields[field.name])} onChange={(event) => { setDraft((current) => ({ ...current, [field.name]: event.target.value })); markDirty(field.name); }} />}
          </label>
          {entity.key === "identity.users" && field.name === "kind" && <p className="field-help">Последнего администратора нельзя снять с роли. Сервер также проверит действующие доступы и назначения.</p>}
          {field.nullable && field.type !== "bool" && <label className="checkbox-row"><input type="checkbox" checked={Boolean(nullFields[field.name])} onChange={(event) => { setNullFields((current) => ({ ...current, [field.name]: event.target.checked })); markDirty(field.name); }} /><span>Установить NULL</span></label>}
          </>}
        </div>)}
        {(changingPhone || changingMaxId) && <p className="system-warning">{changingPhone && "Смена телефона сбросит его подтверждение. "}{changingMaxId && "Смена MAX ID перенесёт роль на другой MAX‑аккаунт и сбросит привязанный номер. Изменение собственного MAX ID может лишить вас доступа к этому кабинету. "}При действующих назначениях сотрудника или доступах жильца сервер отклонит такую правку.</p>}
        <label className="field"><span>Причина изменения · обязательно, 5–2000 символов</span><textarea required minLength={5} maxLength={2000} value={patchReason} onChange={(event) => setPatchReason(event.target.value)} placeholder="Что и почему меняется" /></label>
        <button className="button button--primary" type="submit" disabled={busy || Boolean(pendingCleanup) || dirty.length === 0 || patchReason.trim().length < 5}>{busy ? "Сохраняем…" : "Сохранить с проверкой версии"}</button>
      </form>}
    </section>
    <section className="system-operation system-operation--danger">
      <h3>{deleteModes.includes("soft") ? deleteModes.includes("hard") ? "Архивировать или удалить" : "Архивировать запись" : "Удалить запись"}</h3>
      <p className="system-warning">Операция влияет на данные сервиса. Полное удаление нельзя отменить. Сначала проверьте связанные записи и версию снимка.</p>
      {deleteModes.includes("soft") && (entity.key === "housing.companies" || entity.key === "housing.houses") && <p className="field-help">Архивация помечает только выбранную запись: связанные дома, права и проблемы не архивируются автоматически.</p>}
      <div className="system-dependencies"><strong>Прямые связи: {dependencyCount}</strong>{dependencies.length === 0 ? <p className="muted-text">Прямых связей не найдено.</p> : <ul>{dependencies.map((dependency) => <li key={`${dependency.entity}:${dependency.field}`}>{dependency.entity}.{dependency.field}: {dependency.count}</li>)}</ul>}</div>
      <form className="form-stack" onSubmit={(event) => void remove(event)}>
        {deleteModes.length > 1 && <label className="field"><span>Операция</span><select value={deleteMode} onChange={(event) => { setDeleteMode(event.target.value as "soft" | "hard"); setConfirmed(false); setConfirmCount(""); setConfirmHash(""); }}>{deleteModes.includes("soft") && <option value="soft">Архивировать (данные сохранятся)</option>}{deleteModes.includes("hard") && <option value="hard">Удалить навсегда с зависимостями</option>}</select></label>}
        {deleteMode === "hard" && (preview ? <CascadePreview preview={preview} /> : <p className="form-error" role="alert">Сервер не предоставил точный предпросмотр каскада. Обновите запись. Удаление без предпросмотра недоступно.</p>)}
        <label className="field"><span>Причина {deleteMode === "soft" ? "архивации" : "удаления"} · обязательно, 5–2000 символов</span><textarea required minLength={5} maxLength={2000} value={deleteReason} onChange={(event) => setDeleteReason(event.target.value)} placeholder="Зачем нужна эта операция" /></label>
        {deleteMode === "hard" && preview && <>
          <label className="field"><span>Введите количество удаляемых записей: {preview.total}</span><input type="text" inputMode="numeric" value={confirmCount} onChange={(event) => setConfirmCount(event.target.value)} autoComplete="off" spellCheck={false} /></label>
          <label className="field"><span>Введите полный хеш снимка</span><input type="text" value={confirmHash} onChange={(event) => setConfirmHash(event.target.value)} autoComplete="off" spellCheck={false} /></label>
        </>}
        <label className="checkbox-row"><input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} /><span>Я проверил {deleteMode === "soft" ? "запись и подтверждаю архивацию" : "полный состав каскада, изменения связанных записей и подтверждаю необратимое удаление"}</span></label>
        {deleteReadinessIssues.length > 0 && <div className="field-help" role="status"><strong>Чтобы выполнить операцию:</strong><ul>{deleteReadinessIssues.map((issue) => <li key={issue}>{issue}</li>)}</ul></div>}
        <button type="submit" className="button button--soft" disabled={busy || deleteReadinessIssues.length > 0}>{busy ? "Выполняем…" : deleteMode === "soft" ? "Архивировать запись" : "Удалить каскад без восстановления"}</button>
      </form>
    </section>
    {error && <p className="form-error" role="alert">{error}</p>}
  </div>;
}

function SystemOperations({ refreshKey, onOpenOperation }: { refreshKey: number; onOpenOperation: (id: string) => void }) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<AdminSystemOperationsPage | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [retryingId, setRetryingId] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setLoading(true); setError("");
    void listAdminSystemOperations(query, offset, 20)
      .then((result) => { if (!cancelled) setPage(result); })
      .catch((reason) => { if (!cancelled) setError(reason instanceof Error ? reason.message : "Не удалось загрузить журнал"); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [open, query, offset, refreshKey, reloadKey]);

  async function retryCleanup(operationId: string) {
    setRetryingId(operationId); setError(""); setNotice("");
    try {
      const result = await retryAdminSystemFileCleanup(operationId);
      if (result.status === "done") setNotice(`Очистка завершена. Файлов удалено: ${result.deleted}.`);
      else setError(`Очистка не завершена: файлов удалено ${result.deleted}, осталось ошибок ${result.failed}. Повторите позже.`);
      setReloadKey((current) => current + 1);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Не удалось повторить очистку файлов");
    } finally {
      setRetryingId(null);
    }
  }

  return <section className="panel admin-list-panel system-log">
    <div className="section-heading"><div><h2>Журнал системных операций</h2><p>История правок и удалений</p></div><button type="button" className="button button--soft" onClick={() => setOpen((current) => !current)}>{open ? "Скрыть" : "Показать"}</button></div>
    {open && <>
      <p className="field-help">Записи журнала можно исправлять и удалять в системном редакторе. Такая правка сама создаст новую запись журнала.</p>
      <form className="admin-search" onSubmit={(event) => { event.preventDefault(); setOffset(0); setQuery(search.trim()); }}><label className="search-field"><Icon name="search" size={19} /><input type="search" maxLength={100} value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Поиск по журналу" aria-label="Поиск по журналу системных операций" /></label><button type="submit" className="button button--primary">Найти</button></form>
      {loading && <p className="muted-text">Загружаем журнал…</p>}
      {error && <p className="form-error" role="alert">{error}</p>}
      {notice && <p className="form-success" role="status">{notice}</p>}
      {!loading && page?.items.length === 0 && <p className="muted-text">Операций не найдено.</p>}
      {!loading && page?.items.map((operation) => {
        const cleanup = operation.after_data?._file_cleanup as { status?: string; pending?: { id: string }[]; deleted?: number; failed?: number } | undefined;
        const cleanupPending = operation.operation === "hard_delete" && cleanup && cleanup.pending?.length && (cleanup.status === "pending" || cleanup.status === "failed");
        return <details className="admin-action system-log__item" key={operation.id}>
        <summary>{operation.operation} · {operation.entity_key} · {operation.row_key}</summary>
        <dl className="admin-details">
          <div className="admin-details__row"><dt>Время</dt><dd>{displayValue(operation.created_at)}</dd></div>
          <div className="admin-details__row"><dt>Автор</dt><dd>{operation.actor_user_id}</dd></div>
          <div className="admin-details__row"><dt>Причина</dt><dd>{operation.reason}</dd></div>
          <div className="admin-details__row"><dt>ID операции</dt><dd>{operation.id}</dd></div>
          <div className="admin-details__row"><dt>Было</dt><dd className="admin-details__structured">{displayValue(operation.before_data)}</dd></div>
          <div className="admin-details__row"><dt>Стало</dt><dd className="admin-details__structured">{displayValue(operation.after_data)}</dd></div>
          {cleanup && <div className="admin-details__row"><dt>Очистка файлов</dt><dd>{cleanup.status === "done" ? "Завершена" : cleanup.status === "failed" ? "Ошибка, требуется повтор" : "Ожидает выполнения"}; удалено {cleanup.deleted ?? 0}, осталось {cleanup.pending?.length ?? 0}</dd></div>}
        </dl>
        {cleanupPending && <button type="button" className="button button--soft" disabled={retryingId !== null} onClick={() => void retryCleanup(operation.id)}>{retryingId === operation.id ? "Повторяем очистку…" : "Повторить удаление файлов"}</button>}
        <button type="button" className="button button--soft" onClick={() => onOpenOperation(operation.id)}>Изменить или удалить запись</button>
      </details>;
      })}
      {page && page.total > page.limit && <div className="admin-pagination"><button type="button" className="button button--soft" disabled={loading || offset === 0} onClick={() => setOffset(Math.max(0, offset - page.limit))}>Назад</button><span>Страница {Math.floor(offset / page.limit) + 1} из {Math.ceil(page.total / page.limit)}</span><button type="button" className="button button--soft" disabled={loading || offset + page.limit >= page.total} onClick={() => setOffset(offset + page.limit)}>Далее</button></div>}
    </>}
  </section>;
}

export function SystemEditor({ onExit, currentUserId, onOwnAccountChanged }: { onExit: () => void; currentUserId: string; onOwnAccountChanged: () => void }) {
  const [entities, setEntities] = useState<AdminSystemEntity[]>([]);
  const [entityKey, setEntityKey] = useState("");
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<AdminSystemPage | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<AdminSystemDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [noticeIsWarning, setNoticeIsWarning] = useState(false);
  const selectedEntity = entities.find((entity) => entity.key === entityKey);

  useEffect(() => {
    let cancelled = false;
    void getAdminSystemSchema()
      .then((items) => { if (!cancelled) setEntities(items); })
      .catch((reason) => { if (!cancelled) setError(reason instanceof Error ? reason.message : "Не удалось загрузить модели"); });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    if (!entityKey) { setPage(null); return; }
    let cancelled = false;
    setLoading(true); setPage(null); setError("");
    void listAdminSystemRows(entityKey, query, offset, 20)
      .then((result) => { if (!cancelled) setPage(result); })
      .catch((reason) => { if (!cancelled) setError(reason instanceof Error ? reason.message : "Не удалось загрузить записи"); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [entityKey, query, offset, refreshKey]);

  useEffect(() => {
    if (!entityKey || !selectedId) { setDetail(null); return; }
    let cancelled = false;
    setDetailLoading(true); setDetail(null);
    void getAdminSystemRow(entityKey, selectedId)
      .then((result) => { if (!cancelled) setDetail(result); })
      .catch((reason) => { if (!cancelled) setError(reason instanceof Error ? reason.message : "Не удалось открыть запись"); })
      .finally(() => { if (!cancelled) setDetailLoading(false); });
    return () => { cancelled = true; };
  }, [entityKey, selectedId, refreshKey]);

  function chooseEntity(key: string) {
    setEntityKey(key); setSelectedId(null); setSearch(""); setQuery(""); setOffset(0); setNotice(""); setError("");
  }

  function mutated(message: string, hardDeleted: boolean, warning = false) {
    setNotice(message); setNoticeIsWarning(warning); setError("");
    if (hardDeleted) {
      setSelectedId(null);
      if (page?.items.length === 1 && offset > 0) setOffset(Math.max(0, offset - page.limit));
    }
    setRefreshKey((current) => current + 1);
  }

  return <div className="page page--admin page--system-editor">
    <ScreenHeader title="Системный редактор" subtitle="Расширенное управление данными" icon="shield" />
    <button type="button" className="button button--soft system-back" onClick={onExit}>← Обычный кабинет администратора</button>
    <div className="system-warning system-warning--intro"><strong>Системные операции</strong><br />Здесь можно менять только модели и поля, разрешённые сервером. Каждое изменение требует причины, проверки версии и записывается в отдельный журнал. Это не рабочий экран для повседневных заявок.</div>
    <SystemOperations refreshKey={refreshKey} onOpenOperation={(id) => { chooseEntity("system.admin_operations"); setSelectedId(id); }} />
    <section className="panel admin-list-panel">
      <label className="field"><span>Модель данных</span><select value={entityKey} onChange={(event) => chooseEntity(event.target.value)}><option value="">Выберите таблицу или модель</option>{entities.map((entity) => <option key={entity.key} value={entity.key}>{entity.label} · {entity.key}</option>)}</select></label>
      {selectedEntity && <p className="field-help">Ключ: {selectedEntity.key_fields.join(", ") || "id"}. Полей: {selectedEntity.fields.length}. Допустимые операции: {selectedEntity.delete_modes.map((mode) => mode === "soft" ? "архивация" : "полное удаление").join(", ")}.</p>}
      {entityKey && <>
        <form className="admin-search" onSubmit={(event) => { event.preventDefault(); setSelectedId(null); setOffset(0); setQuery(search.trim()); }}><label className="search-field"><Icon name="search" size={19} /><input type="search" maxLength={100} value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Поиск по записи" aria-label="Поиск по системной модели" /></label><button type="submit" className="button button--primary">Найти</button></form>
        {loading && <p className="muted-text">Загружаем записи…</p>}
        {!loading && page?.items.length === 0 && <p className="muted-text">Записи не найдены.</p>}
        {!loading && page && <div className="admin-records">{page.items.map((item) => <button key={item.id} type="button" className={`admin-record${selectedId === item.id ? " admin-record--active" : ""}`} onClick={() => setSelectedId(item.id)}><span className="admin-record__main"><strong>{rowTitle(item)}</strong><small>{item.id}</small></span><span className="admin-record__meta"><small>Версия {item.etag.slice(0, 10)}…</small></span><Icon name="chevron" size={18} /></button>)}</div>}
        {page && page.total > page.limit && <div className="admin-pagination"><button type="button" className="button button--soft" disabled={loading || offset === 0} onClick={() => { setSelectedId(null); setOffset(Math.max(0, offset - page.limit)); }}>Назад</button><span>Страница {Math.floor(offset / page.limit) + 1} из {Math.ceil(page.total / page.limit)}</span><button type="button" className="button button--soft" disabled={loading || offset + page.limit >= page.total} onClick={() => { setSelectedId(null); setOffset(offset + page.limit); }}>Далее</button></div>}
      </>}
      {error && <p className="form-error" role="alert">{error}</p>}
      {notice && <p className={noticeIsWarning ? "form-error" : "form-success"} role={noticeIsWarning ? "alert" : "status"}>{notice}</p>}
    </section>
    {selectedId && <section className="panel admin-detail-panel" id="system-detail">{detailLoading && <p className="muted-text">Загружаем запись и зависимости…</p>}{detail && selectedEntity && <SystemRecordEditor key={`${entityKey}:${detail.item.id}:${detail.item.etag}:${refreshKey}`} entity={selectedEntity} detail={detail} onRefresh={() => setRefreshKey((current) => current + 1)} onMutated={mutated} currentUserId={currentUserId} onOwnAccountChanged={onOwnAccountChanged} />}</section>}
  </div>;
}
