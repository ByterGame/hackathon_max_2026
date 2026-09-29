import { useEffect, useRef, useState } from "react";

import { downloadFile, listFiles, uploadParentFile, type PrivateFile } from "../features/files/integrations/client_api";
import { isDemoMode, issuesClient } from "../features/issues/integrations/client_api";
import { NotificationToggle } from "../features/notifications/ui/NotificationToggle";
import { formatDate, formatIssueScope, issueStatusLabels, type CloseResult, type House, type Issue, type IssueCategory, type IssueStatus, type Role } from "../features/issues/types";
import { Icon } from "../shared/common_ui/Icon";
import { ScreenHeader } from "../shared/common_ui/ScreenHeader";

function parsePositiveNumbers(value: string): number[] | null {
  if (!value.trim()) return [];
  const parts = value.split(",").map((item) => item.trim());
  if (parts.some((item) => !/^[1-9]\d*$/.test(item))) return null;
  return [...new Set(parts.map(Number))];
}

function parseApartmentPairs(value: string): { entrance: number; number: number }[] | null {
  if (!value.trim()) return [];
  const parts = value.split(",").map((item) => item.trim());
  if (parts.some((item) => !/^[1-9]\d*\/[1-9]\d*$/.test(item))) return null;
  return parts.map((item) => { const [entrance, number] = item.split("/").map(Number); return { entrance, number }; });
}

export function IssueDetail({ issue, house, role, currentUserId, canManageIssues, categories, relatedIssues, onBack, onChanged, onMerged }: { issue: Issue; house: House; role: Role; currentUserId: string; canManageIssues: boolean; categories: IssueCategory[]; relatedIssues: Issue[]; onBack: () => void; onChanged: () => Promise<void>; onMerged: (id: string) => Promise<void> }) {
  const [comment, setComment] = useState("");
  const [status, setStatus] = useState<IssueStatus>(issue.status);
  const [statusNote, setStatusNote] = useState("");
  const [closeResult, setCloseResult] = useState<CloseResult>("solved");
  const [reopenReason, setReopenReason] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [editTitle, setEditTitle] = useState(issue.title);
  const [editCategory, setEditCategory] = useState(issue.category);
  const [editAllHouse, setEditAllHouse] = useState(issue.scope.allHouse);
  const [editEntrances, setEditEntrances] = useState(issue.scope.entrances.join(", "));
  const [editApartments, setEditApartments] = useState(issue.scope.apartments.map((item) => `${item.entrance}/${item.number}`).join(", "));
  const [mergeOtherId, setMergeOtherId] = useState("");
  const [mergeTitle, setMergeTitle] = useState(issue.title);
  const [mergeStatus, setMergeStatus] = useState<Exclude<IssueStatus, "closed">>(issue.status === "closed" ? "open" : issue.status);
  const [mergeNote, setMergeNote] = useState("");
  const [privateFiles, setPrivateFiles] = useState<PrivateFile[]>([]);
  const [messageFiles, setMessageFiles] = useState<Record<string, PrivateFile[]>>({});
  const [fileError, setFileError] = useState("");
  const [commentFile, setCommentFile] = useState<File | null>(null);
  const [pendingCommentId, setPendingCommentId] = useState<string | null>(null);
  const commentKey = useRef<string | null>(null);

  useEffect(() => setStatus(issue.status), [issue.status]);
  useEffect(() => {
    setEditTitle(issue.title);
    setEditCategory(issue.category);
    setEditAllHouse(issue.scope.allHouse);
    setEditEntrances(issue.scope.entrances.join(", "));
    setEditApartments(issue.scope.apartments.map((item) => `${item.entrance}/${item.number}`).join(", "));
    setMergeTitle(issue.title);
    setMergeStatus(issue.status === "closed" ? "open" : issue.status);
  }, [issue.id, issue.version]);
  useEffect(() => {
    if (isDemoMode || !issue.reportIds?.length) return;
    let active = true;
    void Promise.all(issue.reportIds.map((id) => listFiles("issue_report", id)))
      .then((lists) => { if (active) setPrivateFiles(lists.flat()); })
      .catch(() => { if (active) setPrivateFiles([]); });
    return () => { active = false; };
  }, [issue.id, issue.reportIds?.join(",")]);
  useEffect(() => {
    if (isDemoMode || !issue.messages.length) return;
    let active = true;
    void Promise.all(issue.messages.map(async (message) => [message.id, await listFiles("issue_message", message.id)] as const))
      .then((entries) => { if (active) setMessageFiles(Object.fromEntries(entries)); })
      .catch(() => { if (active) setMessageFiles({}); });
    return () => { active = false; };
  }, [issue.id, issue.messages.map((item) => item.id).join(",")]);

  async function run(action: () => Promise<unknown>, after?: () => void) {
    setBusy(true); setError("");
    try { await action(); await onChanged(); after?.(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Действие не выполнено"); }
    finally { setBusy(false); }
  }

  async function submitEdit(event: React.FormEvent) {
    event.preventDefault();
    const entrances = parsePositiveNumbers(editEntrances);
    const apartments = parseApartmentPairs(editApartments);
    if (entrances === null || apartments === null) { setError("Подъезды: 2, 3. Квартиры: 2/24, 3/48"); return; }
    if (!editAllHouse && !entrances.length && !apartments.length) { setError("Укажите область проблемы"); return; }
    await run(() => issuesClient.editIssue(issue.id, issue.version ?? 1, {
      title: editTitle,
      category: editCategory,
      scope: { allHouse: editAllHouse, entrances: editAllHouse ? [] : entrances, apartments: editAllHouse ? [] : apartments },
    }));
  }

  async function submitMerge(event: React.FormEvent) {
    event.preventDefault();
    if (!mergeOtherId || !window.confirm("Объединить эти две карточки? Исходные обращения и обсуждение сохранятся в общей карточке.")) return;
    setBusy(true); setError("");
    try {
      const merged = await issuesClient.mergeIssues(issue.id, mergeOtherId, mergeTitle, mergeStatus, mergeNote);
      await onMerged(merged.id);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Не удалось объединить проблемы"); }
    finally { setBusy(false); }
  }

  async function downloadAttachment(file: PrivateFile) {
    setFileError("");
    try { await downloadFile(file); }
    catch (reason) { setFileError(reason instanceof Error ? reason.message : "Не удалось скачать файл"); }
  }

  async function submitComment(event: React.FormEvent) {
    event.preventDefault();
    if (!comment.trim() && !commentFile && !pendingCommentId) return;
    if (commentFile && issue.status === "closed") { setError("В закрытую проблему нельзя добавить файл"); return; }
    setBusy(true); setError("");
    try {
      let messageId = pendingCommentId;
      if (!messageId) {
        if (!commentKey.current && !isDemoMode) commentKey.current = crypto.randomUUID();
        const result = await issuesClient.addMessage(issue.id, role, comment.trim() || "Вложение", commentKey.current ?? undefined);
        messageId = result.lastMessageId ?? null;
        setPendingCommentId(messageId);
      }
      if (commentFile) {
        if (!messageId) throw new Error("Сообщение отправлено, но сервер не вернул идентификатор для вложения");
        try { await uploadParentFile(commentFile, "issue_message", messageId); }
        catch (reason) {
          const alreadyUploaded = await listFiles("issue_message", messageId).then((items) => items.some((item) => item.original_name === commentFile.name && item.size_bytes === commentFile.size)).catch(() => false);
          if (!alreadyUploaded) throw reason;
        }
      }
      await onChanged();
      setComment("");
      setCommentFile(null);
      setPendingCommentId(null);
      commentKey.current = null;
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Не удалось отправить сообщение или файл"); }
    finally { setBusy(false); }
  }

  const canReopen = role === "resident" && issue.status === "closed" && issue.authorId === currentUserId;

  return (
    <div className="page page--detail">
      <ScreenHeader title="Проблема дома" subtitle={house.address} onBack={onBack} />
      <section className="panel detail-hero"><div className="detail-hero__top"><span className="eyebrow">{issue.category}</span><span className={`status status--${issue.status}`}>{issue.status === "closed" && issue.closeResult === "solved" ? "Решена" : issueStatusLabels[issue.status]}</span></div><h2>{issue.title}</h2><p className="detail-meta"><Icon name="pin" size={17} /> {house.address} · {formatIssueScope(issue.scope)}</p><p>{issue.description}</p><div className="detail-facts"><span><Icon name="people" size={19} /> Поддержали <strong>{issue.supportsCount}</strong></span><span><Icon name="chat" size={19} /> Сообщений <strong>{issue.messages.length}</strong></span><span><Icon name="clock" size={19} /> {formatDate(issue.createdAt)}</span></div>{role === "resident" && issue.status !== "closed" && <button type="button" className={`button button--wide ${issue.supportedByMe ? "button--soft" : "button--primary"}`} disabled={busy || issue.supportedByMe} onClick={() => void run(() => issuesClient.supportIssue(issue.id))}>{issue.supportedByMe ? <><Icon name="check" /> Вы участвуете</> : <><Icon name="people" /> У меня та же проблема</>}</button>}</section>

      <section className="panel notification-panel"><NotificationToggle subject="issue_card" id={issue.id} expanded /></section>

      {privateFiles.length > 0 && <section className="panel attachment-panel"><h3>Вложения</h3><div className="attachment-list">{privateFiles.map((file) => <button key={file.id} type="button" className="button button--soft" onClick={() => void downloadAttachment(file)}><Icon name="paperclip" size={17} /> {file.original_name} · {Math.ceil(file.size_bytes / 1024)} КБ</button>)}</div>{fileError && <p className="form-error" role="alert">{fileError}</p>}</section>}

      <section className="panel timeline-panel"><h3>История</h3><ol className="timeline">{issue.events.map((event) => <li key={event.id}><span className="timeline__dot" /><time>{formatDate(event.createdAt)}</time><div><strong>{event.label}</strong>{event.note && <p>{event.note}</p>}</div></li>)}</ol></section>

      {issue.currentNote && <section className="panel official-panel"><h3>Последнее обновление УК</h3><div className="official-message"><span className="small-icon"><Icon name="building" /></span><div><strong>{house.company}</strong><small>Официальный ответ</small><p>{issue.currentNote}</p></div></div></section>}

      {role === "employee" && canManageIssues && issue.status !== "closed" && <section className="panel form-panel staff-action"><h3>Обновить карточку</h3><p className="section-description">Статус меняется только после сохранения.</p><label className="field"><span>Статус</span><select value={status} onChange={(event) => setStatus(event.target.value as IssueStatus)}>{(Object.keys(issueStatusLabels) as IssueStatus[]).map((value) => <option key={value} value={value}>{issueStatusLabels[value]}</option>)}</select></label>{status === "closed" && <label className="field"><span>Результат закрытия</span><select value={closeResult} onChange={(event) => setCloseResult(event.target.value as CloseResult)}><option value="solved">Решена</option><option value="invalid">Некорректное обращение</option></select></label>}<label className="field"><span>Пояснение {status === "closed" ? "· обязательно" : "· при необходимости"}</span><textarea rows={3} value={statusNote} onChange={(event) => setStatusNote(event.target.value)} placeholder="Что сделано или что происходит сейчас" /></label><button type="button" className="button button--primary button--wide" disabled={busy || (status === "closed" && !statusNote.trim())} onClick={() => void run(() => issuesClient.updateStatus(issue.id, status, statusNote, status === "closed" ? closeResult : undefined), () => setStatusNote(""))}>Сохранить обновление</button></section>}
      {role === "employee" && canManageIssues && issue.status === "closed" && <div className="info-panel closed-info"><Icon name="info" size={20} /> Закрытую карточку может переоткрыть только её автор. Официальный ответ можно добавить в обсуждение.</div>}

      {role === "employee" && canManageIssues && <details className="panel form-panel issue-management"><summary>Изменить название, категорию или область</summary><form onSubmit={(event) => void submitEdit(event)}><label className="field"><span>Название</span><input required value={editTitle} onChange={(event) => setEditTitle(event.target.value)} maxLength={100} /></label><label className="field"><span>Категория</span><select value={editCategory} onChange={(event) => setEditCategory(event.target.value)}>{categories.map((item) => <option key={item.id} value={item.name}>{item.name}</option>)}</select></label><label className="checkbox-row"><input type="checkbox" checked={editAllHouse} onChange={(event) => setEditAllHouse(event.target.checked)} /><span>Весь дом</span></label>{!editAllHouse && <div className="field-grid"><label className="field"><span>Подъезды</span><input value={editEntrances} onChange={(event) => setEditEntrances(event.target.value)} placeholder="2, 3" /></label><label className="field"><span>Квартиры: подъезд/номер</span><input value={editApartments} onChange={(event) => setEditApartments(event.target.value)} placeholder="2/24, 3/48" /></label></div>}<p className="field-help">Описание жителя и исходные обращения не изменятся.</p><button type="submit" className="button button--primary" disabled={busy || !editTitle.trim()}>Сохранить сводку</button></form></details>}

      {role === "employee" && canManageIssues && issue.status !== "closed" && relatedIssues.length > 0 && <details className="panel form-panel issue-management"><summary>Объединить с другой проблемой</summary><form onSubmit={(event) => void submitMerge(event)}><label className="field"><span>Вторая открытая карточка этого дома</span><select required value={mergeOtherId} onChange={(event) => setMergeOtherId(event.target.value)}><option value="">Выберите карточку</option>{relatedIssues.map((item) => <option key={item.id} value={item.id}>{item.title}</option>)}</select></label><label className="field"><span>Итоговое название</span><input required value={mergeTitle} onChange={(event) => setMergeTitle(event.target.value)} /></label><label className="field"><span>Итоговый статус</span><select value={mergeStatus} onChange={(event) => setMergeStatus(event.target.value as Exclude<IssueStatus, "closed">)}>{(["open", "reviewing", "needs_info", "in_progress"] as const).map((value) => <option key={value} value={value}>{issueStatusLabels[value]}</option>)}</select></label><label className="field"><span>Пояснение</span><textarea value={mergeNote} onChange={(event) => setMergeNote(event.target.value)} rows={2} /></label><p className="field-help">Поддержки, обращения и обсуждение сохранятся в общей карточке.</p><button type="submit" className="button button--soft" disabled={busy || !mergeOtherId || !mergeTitle.trim()}>Объединить карточки</button></form></details>}

      {canReopen && <section className="panel form-panel reopen-panel"><h3>Проблема осталась?</h3><p className="section-description">Автор может переоткрыть карточку, указав причину.</p><label className="field"><span>Что не решено</span><textarea value={reopenReason} onChange={(event) => setReopenReason(event.target.value)} rows={2} placeholder="Например: лифт снова не работает" /></label><button type="button" className="button button--soft" disabled={busy || !reopenReason.trim()} onClick={() => void run(() => issuesClient.reopenIssue(issue.id, reopenReason), () => setReopenReason(""))}>Переоткрыть</button></section>}

      <section className="panel discussion-panel">
        <div className="section-heading"><h3>Обсуждение</h3><span className="count-badge">{issue.messages.length}</span></div>
        <div className="message-list">{issue.messages.length ? issue.messages.map((message) => <article key={message.id} className={`message ${message.kind === "official_uk" ? "message--official" : ""}`}><span className="message__avatar"><Icon name={message.kind === "official_uk" ? "building" : "user"} size={19} /></span><div><div className="message__heading"><strong>{message.author}</strong>{message.kind === "official_uk" && <small>УК</small>}<time>{formatDate(message.createdAt)}</time></div><p>{message.body}</p>{messageFiles[message.id]?.length > 0 && <div className="message-attachments">{messageFiles[message.id].map((file) => <button key={file.id} type="button" className="notification-link" onClick={() => void downloadAttachment(file)}><Icon name="paperclip" size={15} /> {file.original_name}</button>)}</div>}</div></article>) : <p className="muted-text">Пока нет сообщений.</p>}</div>
        {(role === "resident" || canManageIssues) && <form className="comment-form" onSubmit={(event) => void submitComment(event)}><div className="comment-form__row"><input aria-label={role === "employee" ? "Официальный ответ" : "Комментарий"} value={comment} onChange={(event) => setComment(event.target.value)} placeholder={role === "employee" ? "Написать официальный ответ…" : "Добавить комментарий…"} /><button type="submit" className="icon-button icon-button--blue" aria-label="Отправить" disabled={busy || (!comment.trim() && !commentFile && !pendingCommentId)}><Icon name="send" size={19} /></button></div>{!isDemoMode && issue.status !== "closed" && <label className="comment-file">Прикрепить файл к сообщению<input type="file" accept="image/jpeg,image/png,image/webp,application/pdf,video/mp4,video/quicktime" onChange={(event) => { const file = event.target.files?.[0] ?? null; if (file && file.size > 8 * 1024 * 1024) { setError("Файл должен быть не больше 8 МБ"); return; } setCommentFile(file); }} /></label>}{commentFile && <small>Выбран файл: {commentFile.name}</small>}{pendingCommentId && <small>Сообщение уже отправлено; повторное нажатие завершит только файл.</small>}</form>}
      </section>
      {fileError && <p className="form-error" role="alert">{fileError}</p>}
      {error && <p className="form-error" role="alert">{error}</p>}
    </div>
  );
}
