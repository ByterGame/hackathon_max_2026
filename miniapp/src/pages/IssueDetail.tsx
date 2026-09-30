import { useEffect, useRef, useState } from "react";

import { PrivateAttachmentPreview, SelectedAttachmentPreview } from "../features/files/AttachmentPreview";
import { MAX_FILE_BYTES, MAX_FILE_SIZE_LABEL, SUPPORTED_FILE_FORMATS } from "../features/files/file_rules";
import { listFiles, uploadParentFile, type PrivateFile } from "../features/files/integrations/client_api";
import { isDemoMode, issuesClient } from "../features/issues/integrations/client_api";
import { suggestStaffMerges, type StaffMergeSuggestion } from "../features/issues/integrations/staff_merge_api";
import { NotificationToggle } from "../features/notifications/ui/NotificationToggle";
import { formatDate, formatIssueScope, issueStatusLabels, type CloseResult, type House, type Issue, type IssueCategory, type IssueStatus, type Role } from "../features/issues/types";
import { Icon } from "../shared/common_ui/Icon";
import { ScreenHeader } from "../shared/common_ui/ScreenHeader";
import { formatCount } from "../shared/format_count";
import "./issue-detail.css";

function parsePositiveNumbers(value: string): number[] | null {
  if (!value.trim()) return [];
  const parts = value.split(",").map((item) => item.trim());
  if (parts.some((item) => !/^[1-9]\d*$/.test(item))) return null;
  return [...new Set(parts.map(Number))];
}

function displayPhone(value?: string): string | null {
  if (!value) return null;
  return /^7\d{10}$/.test(value) ? `+${value}` : value;
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
  const [editSummaryDescription, setEditSummaryDescription] = useState(issue.summaryDescription || issue.description);
  const [editCategory, setEditCategory] = useState(issue.category);
  const [editAllHouse, setEditAllHouse] = useState(issue.scope.allHouse);
  const [editEntrances, setEditEntrances] = useState(issue.scope.entrances.join(", "));
  const [editApartments, setEditApartments] = useState(issue.scope.apartments.map((item) => item.number).join(", "));
  const [mergeOtherId, setMergeOtherId] = useState("");
  const [mergeTitle, setMergeTitle] = useState(issue.title);
  const [mergeStatus, setMergeStatus] = useState<Exclude<IssueStatus, "closed">>(issue.status === "closed" ? "open" : issue.status);
  const [mergeNote, setMergeNote] = useState("");
  const [mergeConfirmed, setMergeConfirmed] = useState(false);
  const [mergeSuggestions, setMergeSuggestions] = useState<StaffMergeSuggestion | null>(null);
  const [mergeSuggestionBusy, setMergeSuggestionBusy] = useState(false);
  const reportFilesKey = `${issue.id}:${(issue.reportIds ?? []).join(",")}`;
  const [reportFiles, setReportFiles] = useState<{ key: string; files: PrivateFile[]; failed: boolean }>({ key: reportFilesKey, files: [], failed: false });
  const [messageFiles, setMessageFiles] = useState<Record<string, PrivateFile[]>>({});
  const [messageFilesFailed, setMessageFilesFailed] = useState(false);
  const [fileLoadRevision, setFileLoadRevision] = useState(0);
  const [commentFiles, setCommentFiles] = useState<File[]>([]);
  const [pendingCommentId, setPendingCommentId] = useState<string | null>(null);
  const commentKey = useRef<string | null>(null);
  const commentFileInput = useRef<HTMLInputElement | null>(null);
  const replyInput = useRef<HTMLTextAreaElement | null>(null);
  const errorNotice = useRef<HTMLParagraphElement | null>(null);
  const statusSection = useRef<HTMLElement | null>(null);
  const uploadedCommentFiles = useRef(new Map<File, PrivateFile>());

  useEffect(() => setStatus(issue.status), [issue.status]);
  useEffect(() => { if (error) errorNotice.current?.scrollIntoView({ behavior: "smooth", block: "center" }); }, [error]);
  useEffect(() => {
    setComment("");
    setCommentFiles([]);
    setPendingCommentId(null);
    setError("");
    commentKey.current = null;
    uploadedCommentFiles.current.clear();
    if (commentFileInput.current) commentFileInput.current.value = "";
  }, [issue.id]);
  useEffect(() => {
    setEditTitle(issue.title);
    setEditSummaryDescription(issue.summaryDescription || issue.description);
    setEditCategory(issue.category);
    setEditAllHouse(issue.scope.allHouse);
    setEditEntrances(issue.scope.entrances.join(", "));
    setEditApartments(issue.scope.apartments.map((item) => item.number).join(", "));
    setMergeTitle(issue.title);
    setMergeStatus(issue.status === "closed" ? "open" : issue.status);
    setMergeSuggestions(null);
    setMergeOtherId("");
    setMergeConfirmed(false);
  }, [issue.id, issue.version]);
  useEffect(() => {
    setReportFiles({ key: reportFilesKey, files: [], failed: false });
    if (isDemoMode || !issue.reportIds?.length) return;
    let active = true;
    void Promise.allSettled(issue.reportIds.map((id) => listFiles("issue_report", id)))
      .then((results) => {
        if (!active) return;
        setReportFiles({
          key: reportFilesKey,
          files: results.flatMap((result) => result.status === "fulfilled" ? result.value : []),
          failed: results.some((result) => result.status === "rejected"),
        });
      });
    return () => { active = false; };
  }, [reportFilesKey, fileLoadRevision]);
  useEffect(() => {
    setMessageFiles({});
    setMessageFilesFailed(false);
    if (isDemoMode || !issue.messages.length) return;
    let active = true;
    void Promise.allSettled(issue.messages.map(async (message) => [message.id, await listFiles("issue_message", message.id)] as const))
      .then((results) => {
        if (!active) return;
        setMessageFiles(Object.fromEntries(results.flatMap((result) => result.status === "fulfilled" ? [result.value] : [])));
        setMessageFilesFailed(results.some((result) => result.status === "rejected"));
      });
    return () => { active = false; };
  }, [issue.id, issue.messages.map((item) => item.id).join(","), fileLoadRevision]);

  async function run(action: () => Promise<unknown>, after?: () => void) {
    setBusy(true); setError("");
    try { await action(); await onChanged(); after?.(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Действие не выполнено"); }
    finally { setBusy(false); }
  }

  async function submitEdit(event: React.FormEvent) {
    event.preventDefault();
    const entrances = parsePositiveNumbers(editEntrances);
    const apartmentNumbers = parsePositiveNumbers(editApartments);
    if (entrances === null || apartmentNumbers === null) { setError("Подъезды: 2, 3. Квартиры: 24, 48"); return; }
    const apartments = apartmentNumbers.map((number) => ({ number }));
    if (!editAllHouse && !entrances.length && !apartments.length) { setError("Укажите область проблемы"); return; }
    await run(() => issuesClient.editIssue(issue.id, issue.version ?? 1, {
      title: editTitle,
      summaryDescription: editSummaryDescription,
      category: editCategory,
      scope: { allHouse: editAllHouse, entrances: editAllHouse ? [] : entrances, apartments: editAllHouse ? [] : apartments },
    }));
  }

  async function submitMerge(event: React.FormEvent) {
    event.preventDefault();
    const other = relatedIssues.find((item) => item.id === mergeOtherId);
    if (!other) { setError("Выберите вторую открытую карточку этого дома"); return; }
    if (!mergeConfirmed) { setError("Подтвердите, что проверили обе карточки перед объединением"); return; }
    setBusy(true); setError("");
    try {
      const merged = await issuesClient.mergeIssues(issue.id, mergeOtherId, mergeTitle, mergeStatus, mergeNote);
      await onMerged(merged.id);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Не удалось объединить проблемы"); }
    finally { setBusy(false); }
  }

  async function findMergeSuggestions() {
    setMergeSuggestionBusy(true);
    setError("");
    try {
      const result = isDemoMode
        ? { similar_card_ids: relatedIssues.filter((item) => item.category === issue.category).slice(0, 5).map((item) => item.id), source: "local" as const }
        : await suggestStaffMerges(issue.id);
      setMergeSuggestions(result);
      const firstVisible = result.similar_card_ids.find((id) => relatedIssues.some((item) => item.id === id));
      if (firstVisible) { setMergeOtherId(firstVisible); setMergeConfirmed(false); }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Не удалось найти похожие проблемы");
    } finally {
      setMergeSuggestionBusy(false);
    }
  }

  async function submitComment(event: React.FormEvent) {
    event.preventDefault();
    if (!comment.trim() && !commentFiles.length && !pendingCommentId) return;
    if (commentFiles.length && issue.status === "closed") { setError("В закрытую проблему нельзя добавить файл"); return; }
    setBusy(true); setError("");
    let messageId = pendingCommentId;
    try {
      if (!messageId) {
        if (!commentKey.current && !isDemoMode) commentKey.current = crypto.randomUUID();
        const result = await issuesClient.addMessage(issue.id, role, comment.trim() || "Вложение", commentKey.current ?? undefined);
        messageId = result.lastMessageId ?? null;
        setPendingCommentId(messageId);
      }
      if (commentFiles.length && !messageId) throw new Error("Сообщение отправлено, но сервер не вернул идентификатор для вложений");
      for (const file of commentFiles) {
        if (uploadedCommentFiles.current.has(file)) continue;
        if (!messageId) throw new Error("Не удалось определить сообщение для вложений");
        const attachmentMessageId = messageId;
        let uploaded: PrivateFile;
        try { uploaded = await uploadParentFile(file, "issue_message", attachmentMessageId); }
        catch (reason) {
          const existing = await listFiles("issue_message", attachmentMessageId).catch(() => []);
          const matching = existing.find((item) => item.original_name === file.name && item.size_bytes === file.size);
          if (!matching) throw reason;
          uploaded = matching;
        }
        uploadedCommentFiles.current.set(file, uploaded);
        setMessageFiles((current) => {
          const previous = current[attachmentMessageId] ?? [];
          return { ...current, [attachmentMessageId]: previous.some((item) => item.id === uploaded.id) ? previous : [...previous, uploaded] };
        });
      }
      await onChanged();
      setComment("");
      setCommentFiles([]);
      uploadedCommentFiles.current.clear();
      if (commentFileInput.current) commentFileInput.current.value = "";
      setPendingCommentId(null);
      commentKey.current = null;
    } catch (reason) {
      const detail = reason instanceof Error ? reason.message : "Не удалось отправить сообщение или файл";
      setError(messageId && commentFiles.some((file) => !uploadedCommentFiles.current.has(file))
        ? `Сообщение сохранено, но не все вложения загружены: ${detail}. Нажмите «Отправить» ещё раз — уже отправленное не продублируется.`
        : detail);
    }
    finally { setBusy(false); }
  }

  const canReopen = role === "resident" && issue.status === "closed" && issue.authorId === currentUserId;
  const suggestedRelatedIssues = mergeSuggestions?.similar_card_ids
    .flatMap((id) => relatedIssues.filter((item) => item.id === id)) ?? [];
  const mergeOther = relatedIssues.find((item) => item.id === mergeOtherId);
  const latestOfficialEvent = issue.currentNote
    ? issue.events.slice().reverse().find((event) => event.note?.trim() === issue.currentNote?.trim())
    : undefined;
  const latestOfficialMessage = issue.messages
    .filter((message) => message.kind === "official_uk")
    .sort((left, right) => right.createdAt.localeCompare(left.createdAt))[0];
  const officialReply = latestOfficialMessage?.body || issue.currentNote;
  const officialReplyDate = latestOfficialMessage?.createdAt ?? latestOfficialEvent?.createdAt;
  const discussionMessages = latestOfficialMessage
    ? issue.messages.filter((message) => message.id !== latestOfficialMessage.id)
    : issue.messages;
  const privateFiles = reportFiles.key === reportFilesKey ? reportFiles.files : [];
  const privateFilesFailed = reportFiles.key === reportFilesKey && reportFiles.failed;
  const statusAppearance = issue.status === "closed"
    ? issue.closeResult === "invalid" ? "invalid" : issue.closeResult === "solved" ? "solved" : "closed"
    : issue.status;
  const statusLabel = issue.status === "closed"
    ? issue.closeResult === "invalid" ? "Некорректная" : issue.closeResult === "solved" ? "Решена" : issueStatusLabels.closed
    : issueStatusLabels[issue.status];

  function jumpToStatus(nextStatus?: IssueStatus) {
    if (nextStatus) setStatus(nextStatus);
    statusSection.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function jumpToReply() {
    replyInput.current?.scrollIntoView({ behavior: "smooth", block: "center" });
    replyInput.current?.focus({ preventScroll: true });
  }

  const commentComposer = (role === "resident" || canManageIssues) && <form className={role === "employee" ? "issue-detail__reply-form" : "comment-form"} onSubmit={(event) => void submitComment(event)}>
    {role === "employee"
      ? <div className="issue-detail__official-compose">
        <label htmlFor="issue-official-reply">Официальный ответ</label>
        <textarea id="issue-official-reply" ref={replyInput} value={comment} disabled={busy || Boolean(pendingCommentId)} onChange={(event) => setComment(event.target.value)} placeholder="Введите сообщение для жителей…" rows={3} />
        <button type="submit" className="button button--primary" disabled={busy || (!comment.trim() && !commentFiles.length && !pendingCommentId)}>{busy ? "Отправляем…" : "Отправить ответ"}</button>
      </div>
      : <div className="comment-form__row"><label className="comment-form__label"><span>Ваше сообщение</span><textarea aria-label="Комментарий" value={comment} disabled={busy || Boolean(pendingCommentId)} onChange={(event) => setComment(event.target.value)} placeholder="Напишите сообщение по этой проблеме…" rows={2} /></label><button type="submit" className="icon-button icon-button--blue" aria-label="Отправить" disabled={busy || (!comment.trim() && !commentFiles.length && !pendingCommentId)}><Icon name="send" size={19} /></button></div>}
    {!isDemoMode && issue.status !== "closed" && <label className="comment-file">Прикрепить файлы к сообщению<small>Форматы: {SUPPORTED_FILE_FORMATS} · до {MAX_FILE_SIZE_LABEL} на файл</small><input ref={commentFileInput} type="file" multiple disabled={busy} accept="image/jpeg,image/png,image/webp,application/pdf,video/mp4,video/quicktime" onChange={(event) => {
      const selected = [...(event.target.files ?? [])];
      event.target.value = "";
      if (selected.some((file) => file.size > MAX_FILE_BYTES)) { setError(`Каждый файл должен быть не больше ${MAX_FILE_SIZE_LABEL}`); return; }
      setCommentFiles((current) => {
        const known = new Set(current.map((file) => `${file.name}:${file.size}:${file.lastModified}`));
        return [...current, ...selected.filter((file) => {
          const key = `${file.name}:${file.size}:${file.lastModified}`;
          if (known.has(key)) return false;
          known.add(key);
          return true;
        })];
      });
      setError("");
    }} /></label>}
    {commentFiles.length > 0 && <div className="attachment-list">{commentFiles.map((file) => <SelectedAttachmentPreview key={`${file.name}:${file.size}:${file.lastModified}`} file={file} onRemove={busy || uploadedCommentFiles.current.has(file) ? undefined : () => setCommentFiles((current) => current.filter((item) => item !== file))} />)}</div>}
    {pendingCommentId && <small>Сообщение уже отправлено; повторное нажатие завершит оставшиеся вложения.</small>}
  </form>;

  const historyPanel = <details className="issue-detail__history">
    <summary>История · {formatCount(issue.events.length, ["событие", "события", "событий"])} · {statusLabel}</summary>
    <section className="panel timeline-panel"><h3>История изменений</h3><ol className="timeline">{issue.events.map((event) => <li key={event.id}><span className="timeline__dot" /><time>{formatDate(event.createdAt)}</time><div><strong>{event.label}</strong>{event.note && <p>{event.note}</p>}</div></li>)}</ol></section>
  </details>;

  const discussionPanel = <section className="panel discussion-panel" aria-label="Обсуждение">
    <div className="section-heading"><h3>Обсуждение</h3><span className="count-badge">{discussionMessages.length}</span></div>
    <div className="message-list">{discussionMessages.length ? discussionMessages.map((message) => <article key={message.id} className={`message ${message.kind === "official_uk" ? "message--official" : ""}`}><span className="message__avatar"><Icon name={message.kind === "official_uk" ? "building" : "user"} size={19} /></span><div><div className="message__heading"><strong>{message.author}</strong>{message.kind === "official_uk" && <small>УК</small>}<time>{formatDate(message.createdAt)}</time></div><p>{message.body}</p>{messageFiles[message.id]?.length > 0 && <div className="message-attachments">{messageFiles[message.id].map((file) => <PrivateAttachmentPreview key={file.id} file={file} />)}</div>}</div></article>) : <p className="muted-text">{latestOfficialMessage ? "Других сообщений пока нет." : "Пока нет сообщений."}</p>}</div>
    {messageFilesFailed && <div className="issue-detail__attachment-error" role="status">Не все вложения сообщений загрузились. <button type="button" onClick={() => setFileLoadRevision((current) => current + 1)}>Повторить</button></div>}
    {role === "resident" && commentComposer}
  </section>;

  return (
    <div className={`page page--detail issue-detail${role === "employee" ? " issue-detail--employee" : ""}`}>
      <ScreenHeader title="Проблема дома" subtitle={house.address} onBack={onBack} />
      <div className="issue-detail__desktop-heading" aria-hidden="true">
        <h1>{issue.title}</h1>
        <span className={`status status--${statusAppearance}`}>{statusLabel}</span>
      </div>
      <section className="panel detail-hero">
        <h2>{issue.title}</h2>
        <div className="detail-hero__status-row">
          <span className={`status status--${statusAppearance}`}>{statusLabel}</span>
          {role === "resident" && <span className="detail-hero__scope">{formatIssueScope(issue.scope)}</span>}
        </div>
        <p className="detail-hero__description">{issue.summaryDescription || issue.description}</p>
        {role === "resident" && <span className="detail-hero__support-count">Поддержали {formatCount(issue.supportsCount, ["житель", "жителя", "жителей"])}</span>}
        {role === "resident" && issue.status !== "closed" && <button type="button" className={`button issue-detail__support-button ${issue.supportedByMe ? "button--soft" : "button--primary"}`} disabled={busy || issue.supportedByMe} onClick={() => void run(() => issuesClient.supportIssue(issue.id))}>{issue.supportedByMe ? <><Icon name="check" size={20} /> Вы участвуете</> : <><Icon name="people" size={20} /> У меня та же проблема</>}</button>}
      </section>

      {role === "employee" && canManageIssues && issue.status !== "closed" && <div className="issue-detail__employee-actions" aria-label="Действия с проблемой">
        <button type="button" className="button button--primary" onClick={() => jumpToStatus()}>Изменить статус</button>
        <button type="button" className="button button--soft" onClick={jumpToReply}>Ответить жильцам</button>
        <button type="button" className="button issue-detail__outline-button" onClick={() => jumpToStatus("closed")}>Закрыть проблему</button>
      </div>}
      {role === "employee" && canManageIssues && commentComposer}

      {officialReply && <section className="official-panel" aria-labelledby="issue-official-title">
        <h3 id="issue-official-title"><Icon name="shield" size={20} />Официальный ответ</h3>
        <strong>{house.company}</strong>
        <p>{officialReply}</p>
        {officialReplyDate && <time dateTime={officialReplyDate}>{formatDate(officialReplyDate)}</time>}
        {latestOfficialMessage && messageFiles[latestOfficialMessage.id]?.length > 0 && <div className="message-attachments">{messageFiles[latestOfficialMessage.id].map((file) => <PrivateAttachmentPreview key={file.id} file={file} />)}</div>}
      </section>}
      {error && <p ref={errorNotice} className="form-error" role="alert">{error}</p>}

      {role === "resident" && historyPanel}
      {discussionPanel}

      <details className="panel issue-detail__metadata">
        <summary>Сведения о проблеме</summary>
        <div className="issue-detail__metadata-content">
          <span>Область: {formatIssueScope(issue.scope)}</span>
          <span>Категория: {issue.category}</span>
          <span>Дом: {house.address}</span>
          <span>Сообщений: {issue.messages.length}</span>
          <span>Создана: {formatDate(issue.createdAt)}</span>
        </div>
      </details>

      {role === "employee" && <details className="panel issue-detail__supporters">
        <summary>Поддержали {formatCount(issue.supportsCount, ["житель", "жителя", "жителей"])}</summary>
        {issue.supporters?.length
          ? <ul>{issue.supporters.map((supporter) => <li key={supporter.userId}><strong>{supporter.displayName}</strong><span>{[displayPhone(supporter.phoneNumber), formatDate(supporter.supportedAt)].filter(Boolean).join(" · ")}</span></li>)}</ul>
          : <p className="field-help">{isDemoMode ? "Список имён недоступен в демонстрационном режиме." : "Список поддержавших пока пуст."}</p>}
      </details>}

      {(privateFiles.length > 0 || privateFilesFailed) && <section className="panel attachment-panel"><h3>Вложения</h3>{privateFiles.length > 0 && <div className="attachment-list">{privateFiles.map((file) => <PrivateAttachmentPreview key={file.id} file={file} />)}</div>}{privateFilesFailed && <div className="issue-detail__attachment-error" role="status">Не все вложения удалось загрузить. <button type="button" onClick={() => setFileLoadRevision((current) => current + 1)}>Повторить</button></div>}</section>}

      {issue.reports.length > 0 && <details className="panel original-reports">
        <summary className="original-reports__summary">
          <span className="original-reports__icon"><Icon name="list" size={22} /></span>
          <span className="original-reports__heading"><strong>Исходные обращения</strong><small>Тексты жителей, из которых собрана карточка</small></span>
          <span className="count-badge">{issue.reports.length}</span>
          <Icon name="chevron" size={18} className="original-reports__chevron" />
        </summary>
        <ol className="original-reports__list">{issue.reports.map((report, index) => <li key={issue.reportIds?.[index] ?? index}><span>Обращение {index + 1}</span><p>{report}</p></li>)}</ol>
      </details>}

      <section className="panel notification-panel"><NotificationToggle subject="issue_card" id={issue.id} expanded /></section>

      {role === "employee" && historyPanel}

      {role === "employee" && canManageIssues && issue.status !== "closed" && <section ref={statusSection} className="panel form-panel staff-action"><h3>Обновить карточку</h3><p className="section-description">Статус меняется только после сохранения.</p><label className="field"><span>Статус</span><select value={status} onChange={(event) => setStatus(event.target.value as IssueStatus)}>{(Object.keys(issueStatusLabels) as IssueStatus[]).map((value) => <option key={value} value={value}>{issueStatusLabels[value]}</option>)}</select></label>{status === "closed" && <label className="field"><span>Результат закрытия</span><select value={closeResult} onChange={(event) => setCloseResult(event.target.value as CloseResult)}><option value="solved">Решена</option><option value="invalid">Некорректное обращение</option></select></label>}<label className="field"><span>Пояснение {status === "closed" ? "· обязательно" : "· при необходимости"}</span><textarea rows={3} value={statusNote} onChange={(event) => setStatusNote(event.target.value)} placeholder="Что сделано или что происходит сейчас" /></label><button type="button" className="button button--primary button--wide" disabled={busy || (status === "closed" && !statusNote.trim())} onClick={() => void run(() => issuesClient.updateStatus(issue.id, status, statusNote, status === "closed" ? closeResult : undefined), () => setStatusNote(""))}>Сохранить обновление</button></section>}
      {role === "employee" && canManageIssues && issue.status === "closed" && <div className="info-panel closed-info"><Icon name="info" size={20} /> Закрытую карточку может переоткрыть только её автор. Официальный ответ можно отправить в поле выше.</div>}

      {role === "employee" && canManageIssues && <details className="panel form-panel issue-management"><summary>Изменить сводку, категорию или область</summary><form onSubmit={(event) => void submitEdit(event)}><label className="field"><span>Название</span><input required value={editTitle} onChange={(event) => setEditTitle(event.target.value)} maxLength={100} /></label><label className="field"><span>Сводное описание</span><textarea required value={editSummaryDescription} onChange={(event) => setEditSummaryDescription(event.target.value)} maxLength={1500} rows={4} /></label><label className="field"><span>Категория</span><select value={editCategory} onChange={(event) => setEditCategory(event.target.value)}>{categories.map((item) => <option key={item.id} value={item.name}>{item.name}</option>)}</select></label><label className="checkbox-row"><input type="checkbox" checked={editAllHouse} onChange={(event) => setEditAllHouse(event.target.checked)} /><span>Весь дом</span></label>{!editAllHouse && <div className="field-grid"><label className="field"><span>Подъезды</span><input value={editEntrances} onChange={(event) => setEditEntrances(event.target.value)} placeholder="2, 3" /></label><label className="field"><span>Квартиры</span><input value={editApartments} onChange={(event) => setEditApartments(event.target.value)} placeholder="24, 48" /></label></div>}<p className="field-help">Исходные обращения жителей не изменятся.</p><button type="submit" className="button button--primary" disabled={busy || !editTitle.trim() || !editSummaryDescription.trim()}>Сохранить сводку</button></form></details>}

      {role === "employee" && canManageIssues && issue.status !== "closed" && relatedIssues.length > 0 && <details className="panel form-panel issue-management">
        <summary>Объединить с другой проблемой</summary>
        <form onSubmit={(event) => void submitMerge(event)}>
          <p className="field-help">Похожие квартирные проблемы видит только сотрудник УК. Объединение выполняется после вашего подтверждения.</p>
          <button type="button" className="button button--soft" disabled={busy || mergeSuggestionBusy} onClick={() => void findMergeSuggestions()}>
            {mergeSuggestionBusy ? "Ищем похожие…" : "Найти похожие проблемы"}
          </button>
          {mergeSuggestions && <div className="field-help" aria-live="polite">
            {suggestedRelatedIssues.length > 0
              ? <><strong>Возможные совпадения:</strong> {suggestedRelatedIssues.map((item) => `${item.title} (${formatIssueScope(item.scope)})`).join("; ")}. {mergeSuggestions.source === "gigachat" ? "Предложено ИИ." : "Подбор по словам: проверьте особенно внимательно."}</>
              : mergeSuggestions.source === "gigachat" ? "ИИ не нашёл похожих открытых карточек." : "Похожих карточек по словам не найдено. ИИ не использовался."}
          </div>}
          <label className="field"><span>Вторая открытая карточка этого дома</span>
            <select required value={mergeOtherId} onChange={(event) => { setMergeOtherId(event.target.value); setMergeConfirmed(false); }}>
              <option value="">Выберите карточку</option>
              {relatedIssues.map((item) => <option key={item.id} value={item.id}>{item.title} · {formatIssueScope(item.scope)}</option>)}
            </select>
          </label>
          <label className="field"><span>Итоговое название</span><input required value={mergeTitle} onChange={(event) => setMergeTitle(event.target.value)} /></label>
          <label className="field"><span>Итоговый статус</span><select value={mergeStatus} onChange={(event) => setMergeStatus(event.target.value as Exclude<IssueStatus, "closed">)}>{(["open", "reviewing", "needs_info", "in_progress"] as const).map((value) => <option key={value} value={value}>{issueStatusLabels[value]}</option>)}</select></label>
          <label className="field"><span>Пояснение</span><textarea value={mergeNote} onChange={(event) => setMergeNote(event.target.value)} rows={2} /></label>
          <p className="field-help">Поддержки, обращения и обсуждение сохранятся в общей карточке.</p>
          {mergeOther && <div className="issue-management__merge-preview" aria-label="Карточки для объединения">
            {[issue, mergeOther].map((card, index) => <div key={card.id}><small>{index === 0 ? "Основная карточка" : "Вторая карточка"}</small><strong>{card.title}</strong><span>Область: {formatIssueScope(card.scope)}</span><p>{card.summaryDescription || card.description}</p></div>)}
          </div>}
          {mergeOther && <label className="checkbox-row issue-management__merge-confirm"><input type="checkbox" checked={mergeConfirmed} onChange={(event) => setMergeConfirmed(event.target.checked)} /><span>Я проверил обе карточки и хочу объединить их</span></label>}
          <button type="submit" className="button button--soft" disabled={busy || !mergeOtherId || !mergeTitle.trim() || !mergeConfirmed}>Объединить карточки</button>
        </form>
      </details>}

      {canReopen && <section className="panel form-panel reopen-panel"><h3>Проблема осталась?</h3><p className="section-description">Автор может переоткрыть карточку, указав причину.</p><label className="field"><span>Что не решено</span><textarea value={reopenReason} onChange={(event) => setReopenReason(event.target.value)} rows={2} placeholder="Например: лифт снова не работает" /></label><button type="button" className="button button--soft" disabled={busy || !reopenReason.trim()} onClick={() => void run(() => issuesClient.reopenIssue(issue.id, reopenReason), () => setReopenReason(""))}>Переоткрыть</button></section>}

    </div>
  );
}
