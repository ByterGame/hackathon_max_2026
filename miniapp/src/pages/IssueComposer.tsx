import { useEffect, useRef, useState } from "react";

import { getDraft, listDrafts, saveDraft, submitDraft, type DraftData } from "../features/drafts/integrations/client_api";
import { SelectedAttachmentPreview } from "../features/files/AttachmentPreview";
import { MAX_FILE_BYTES, MAX_FILE_SIZE_LABEL, SUPPORTED_FILE_FORMATS } from "../features/files/file_rules";
import { attachFile, listFiles, uploadFile } from "../features/files/integrations/client_api";
import { isDemoMode, issuesClient } from "../features/issues/integrations/client_api";
import { formatIssueScope, type CreateIssueInput, type House, type Issue, type IssueCategory } from "../features/issues/types";
import { IssueCard } from "../features/issues/ui/IssueCard";
import { Icon } from "../shared/common_ui/Icon";
import { ScreenHeader } from "../shared/common_ui/ScreenHeader";

function parseNumbers(value: string): number[] | null {
  if (!value.trim()) return [];
  const parts = value.split(",").map((part) => part.trim());
  if (parts.some((part) => !/^[1-9]\d*$/.test(part))) return null;
  return [...new Set(parts.map(Number))];
}

export function IssueComposer({ house, categories, onBack, onOpenIssue }: { house: House; categories: IssueCategory[]; onBack: () => void; onOpenIssue: (id: string) => void }) {
  const [step, setStep] = useState<1 | 2>(1);
  const [category, setCategory] = useState("");
  const [description, setDescription] = useState("");
  const [summaryDescription, setSummaryDescription] = useState("");
  const [title, setTitle] = useState("");
  const [allHouse, setAllHouse] = useState(false);
  const [entrances, setEntrances] = useState("");
  const [apartments, setApartments] = useState("");
  const [similar, setSimilar] = useState<Issue[]>([]);
  const [suggestionSource, setSuggestionSource] = useState<"gigachat" | "local" | null>(null);
  const [suggestedTitle, setSuggestedTitle] = useState("");
  const [descriptionCheck, setDescriptionCheck] = useState<"ok" | "warning" | "not_checked">("not_checked");
  const [descriptionWarning, setDescriptionWarning] = useState<string | null>(null);
  const [draft, setDraft] = useState<CreateIssueInput | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [sharedDraft, setSharedDraft] = useState<DraftData | null>(null);
  const sharedDraftRef = useRef<DraftData | null>(null);
  const [draftNotice, setDraftNotice] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const fileInput = useRef<HTMLInputElement | null>(null);
  const uploadedFiles = useRef(new Map<File, string>());
  const attachedFiles = useRef(new Set<string>());
  const [createdIssue, setCreatedIssue] = useState<Issue | null>(null);
  const createKey = useRef<string | null>(null);
  const [supportedIssueId, setSupportedIssueId] = useState<string | null>(null);
  const [supportReportId, setSupportReportId] = useState<string | null>(null);
  const supportKey = useRef<string | null>(null);
  const editedByUser = useRef(false);

  function removeSelectedFile(file: File) {
    const fileId = uploadedFiles.current.get(file);
    if (fileId && attachedFiles.current.has(fileId)) {
      setError("Файл уже прикреплён к карточке и не может быть убран из выбора");
      return;
    }
    setFiles((current) => current.filter((item) => item !== file));
    uploadedFiles.current.delete(file);
    if (fileInput.current) fileInput.current.value = "";
    setError("");
  }

  useEffect(() => {
    if (isDemoMode) return;
    let active = true;
    void listDrafts("issue_card").then((items) => {
      if (!active) return;
      const saved = items.find((item) => !item.submitted_at && item.payload.house_id === house.id)
        ?? items.find((item) => !item.submitted_at && !item.payload.house_id);
      if (!saved) return;
      sharedDraftRef.current = saved;
      setSharedDraft(saved);
      if (editedByUser.current) return;
      const payload = saved.payload;
      const savedCategory = categories.find((item) => item.id === payload.category_id);
      setCategory(savedCategory?.name ?? "");
      setTitle(typeof payload.title === "string" ? payload.title : "");
      setDescription(typeof payload.description === "string" ? payload.description : "");
      setSummaryDescription(typeof payload.summary_description === "string" ? payload.summary_description : "");
      setAllHouse(payload.scope_all_house === true);
      if (Array.isArray(payload.target_entrances)) setEntrances(payload.target_entrances.filter((item): item is number => typeof item === "number").join(", "));
      if (Array.isArray(payload.target_apartments)) setApartments(payload.target_apartments.flatMap((item) => typeof item === "object" && item !== null && typeof item.apartment_number === "number" ? [String(item.apartment_number)] : []).join(", "));
      setDraftNotice("Восстановлен общий черновик MAX и бота.");
    }).catch(() => { if (active) setDraftNotice("Не удалось загрузить общий черновик. Можно продолжить без него."); });
    return () => { active = false; };
  }, [house.id, categories]);

  function draftPayload(): Record<string, unknown> {
    const categoryId = categories.find((item) => item.name === category)?.id;
    const parsedEntrances = parseNumbers(entrances);
    const parsedApartments = parseNumbers(apartments);
    return {
      house_id: house.id,
      ...(categoryId ? { category_id: categoryId } : {}),
      ...(title.trim() ? { title: title.trim() } : {}),
      ...(description.trim() ? { description: description.trim() } : {}),
      ...(summaryDescription.trim() ? { summary_description: summaryDescription.trim() } : {}),
      scope_all_house: allHouse,
      ...(parsedEntrances !== null ? { target_entrances: allHouse ? [] : parsedEntrances } : {}),
      ...(parsedApartments !== null ? { target_apartments: allHouse ? [] : parsedApartments.map((apartment_number) => ({ apartment_number })) } : {}),
    };
  }

  async function persistDraft(payload = draftPayload()): Promise<DraftData | null> {
    if (isDemoMode) return null;
    const saved = await saveDraft("issue_card", payload, sharedDraftRef.current);
    sharedDraftRef.current = saved;
    setSharedDraft(saved);
    setDraftNotice("Черновик сохранён и доступен в боте MAX.");
    return saved;
  }

  async function savePartialDraft() {
    setBusy(true); setError("");
    try { await persistDraft(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Не удалось сохранить черновик"); }
    finally { setBusy(false); }
  }

  async function review(event: React.FormEvent) {
    event.preventDefault();
    const parsedEntrances = parseNumbers(entrances);
    const parsedApartments = parseNumbers(apartments);
    if (parsedEntrances === null || parsedApartments === null) { setError("Подъезды: 2, 3. Квартиры: 24, 48"); return; }
    if (!allHouse && !parsedEntrances.length && !parsedApartments.length) { setError("Укажите хотя бы один подъезд или квартиру"); return; }
    const proposedTitle = title.trim() || description.trim().split(/[.!?\n]/)[0].slice(0, 100);
    if (!proposedTitle) { setError("Опишите проблему"); return; }
    const input: CreateIssueInput = {
      houseId: house.id,
      title: proposedTitle,
      category,
      description: description.trim(),
      scope: { allHouse, entrances: allHouse ? [] : parsedEntrances, apartments: allHouse ? [] : parsedApartments.map((number) => ({ number })) },
    };
    setDraft(input);
    setBusy(true);
    setError("");
    try {
      const suggestion = await issuesClient.suggestIssue(input);
      setSimilar(suggestion.similarIssues);
      setSuggestionSource(suggestion.source);
      setSuggestedTitle(suggestion.suggestedTitle);
      setSummaryDescription(suggestion.summaryDescription ?? input.description);
      setDescriptionCheck(suggestion.descriptionCheck);
      setDescriptionWarning(suggestion.descriptionWarning);
      setTitle(title.trim() || suggestion.suggestedTitle || proposedTitle);
      if (!isDemoMode) {
        try { await persistDraft({ ...draftPayload(), title: title.trim() || suggestion.suggestedTitle || proposedTitle, summary_description: suggestion.summaryDescription ?? input.description }); }
        catch (reason) { setDraftNotice(`Черновик не сохранён: ${reason instanceof Error ? reason.message : "ошибка сервера"}`); }
      }
      setStep(2);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Не удалось проверить похожие проблемы"); }
    finally { setBusy(false); }
  }

  async function create() {
    if (!draft) return;
    if (!summaryDescription.trim()) { setError("Проверьте сводное описание проблемы"); return; }
    if (summaryDescription.trim().length > 1500) { setError("Сводное описание не должно превышать 1500 символов"); return; }
    setBusy(true); setError("");
    try {
      let saved = sharedDraftRef.current;
      if (!isDemoMode && !createdIssue && (files.length > 0 || saved)) saved = await persistDraft({ ...draftPayload(), title: title.trim() });
      if (!isDemoMode && files.length && !saved) saved = await persistDraft({ ...draftPayload(), title: title.trim() });
      if (!isDemoMode && saved) {
        for (const file of files) {
          if (uploadedFiles.current.has(file)) continue;
          uploadedFiles.current.set(file, (await uploadFile(file, saved.id)).id);
        }
      }
      if (!createKey.current && !isDemoMode) createKey.current = crypto.randomUUID();
      const issue = createdIssue ?? await issuesClient.createIssue({ ...draft, title: title.trim(), summaryDescription: summaryDescription.trim() }, createKey.current ?? undefined);
      setCreatedIssue(issue);
      if (!isDemoMode && files.length) {
        const reportId = issue.reportIds?.[0];
        if (!reportId) throw new Error("Карточка создана, но сервер не вернул обращение для вложений");
        for (const file of files) {
          const fileId = uploadedFiles.current.get(file);
          if (!fileId || attachedFiles.current.has(fileId)) continue;
          try { await attachFile(fileId, "issue_report", reportId); }
          catch (reason) {
            const alreadyAttached = await listFiles("issue_report", reportId).then((items) => items.some((item) => item.id === fileId)).catch(() => false);
            if (!alreadyAttached) throw reason;
          }
          attachedFiles.current.add(fileId);
        }
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
      onOpenIssue(issue.id);
    }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Не удалось создать проблему"); }
    finally { setBusy(false); }
  }

  async function support(issueId: string) {
    if (!draft) return;
    setBusy(true); setError("");
    try {
      if (supportedIssueId && supportedIssueId !== issueId) throw new Error("Вы уже поддержали другую карточку; откройте её из списка");
      let saved = sharedDraftRef.current;
      if (!isDemoMode && !supportedIssueId && (files.length || saved)) saved = await persistDraft(draftPayload());
      if (!isDemoMode && files.length && !saved) saved = await persistDraft(draftPayload());
      if (!isDemoMode && saved) {
        for (const file of files) {
          if (uploadedFiles.current.has(file)) continue;
          uploadedFiles.current.set(file, (await uploadFile(file, saved.id)).id);
        }
      }
      if (!supportKey.current && !isDemoMode) supportKey.current = crypto.randomUUID();
      let reportId = supportReportId;
      if (!supportedIssueId) {
        const result = await issuesClient.supportIssue(issueId, draft.description, supportKey.current ?? undefined);
        setSupportedIssueId(issueId);
        reportId = result.reportId ?? null;
        setSupportReportId(reportId);
      }
      if (!isDemoMode && files.length) {
        if (!reportId) throw new Error("Поддержка учтена, но сервер не вернул идентификатор обращения для файлов");
        for (const file of files) {
          const fileId = uploadedFiles.current.get(file);
          if (!fileId || attachedFiles.current.has(fileId)) continue;
          try { await attachFile(fileId, "issue_report", reportId); }
          catch (reason) {
            const alreadyAttached = await listFiles("issue_report", reportId).then((items) => items.some((item) => item.id === fileId)).catch(() => false);
            if (!alreadyAttached) throw reason;
          }
          attachedFiles.current.add(fileId);
        }
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
      onOpenIssue(issueId);
    }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Не удалось поддержать проблему"); }
    finally { setBusy(false); }
  }

  return (
    <div className="page page--form">
      <ScreenHeader title="Сообщить о проблеме" subtitle={step === 1 ? "Шаг 1 из 2 · Описание" : "Шаг 2 из 2 · Проверка"} onBack={step === 2 ? () => setStep(1) : onBack} />
      <div className="steps" aria-label="Шаги подачи проблемы"><span className={step === 1 ? "is-active" : "is-done"}>1 <small>Описание</small></span><i /><span className={step === 2 ? "is-active" : ""}>2 <small>Проверка</small></span></div>

      {step === 1 ? <form className="form-stack" onInputCapture={() => { editedByUser.current = true; }} onSubmit={(event) => void review(event)}>
        <section className="panel form-panel"><h2>Где возникла проблема?</h2><p className="section-description">{house.address}</p><label className="field"><span>Категория</span><select required value={category} onChange={(event) => { editedByUser.current = true; setCategory(event.target.value); }}><option value="">Выберите категорию</option>{categories.map((item) => <option key={item.id} value={item.name}>{item.name}</option>)}</select></label></section>
        <section className="panel form-panel"><h2>Что произошло?</h2><p className="section-description">Опишите, что не работает и где это находится.</p><label className="field"><span>Ваше описание</span><textarea required maxLength={1500} value={description} onChange={(event) => { setDescription(event.target.value); setSummaryDescription(""); }} placeholder="Например: лифт в подъезде №2 не реагирует на вызов..." rows={5} /><small>{description.length}/1500</small></label><label className="field"><span>Краткая формулировка <em>можно заполнить позже</em></span><input maxLength={100} value={title} onChange={(event) => setTitle(event.target.value)} placeholder="Например: не работает лифт" /></label></section>
        <section className="panel form-panel"><h2>Кого затрагивает?</h2><label className="checkbox-row"><input type="checkbox" checked={allHouse} onChange={(event) => setAllHouse(event.target.checked)} /><span>Весь дом</span></label>{!allHouse && <div className="field-grid"><label className="field"><span>Подъезды</span><input inputMode="text" value={entrances} onChange={(event) => setEntrances(event.target.value)} placeholder="Например: 2, 3" /></label><label className="field"><span>Номера квартир</span><input inputMode="text" value={apartments} onChange={(event) => setApartments(event.target.value)} placeholder="Например: 24, 48" /></label></div>}<p className="field-help">Можно указать несколько подъездов и квартир этого дома.</p></section>
        <section className="panel attachment-placeholder"><span className="small-icon"><Icon name="paperclip" /></span><div><strong>Фото, видео и документы</strong>{isDemoMode ? <p>В демо вложения не отправляются.</p> : <><p>Форматы: {SUPPORTED_FILE_FORMATS} · до {MAX_FILE_SIZE_LABEL} на файл. Доступ сохраняется вместе с проблемой.</p><input ref={fileInput} type="file" multiple accept="image/jpeg,image/png,image/webp,application/pdf,video/mp4,video/quicktime" aria-label="Выбрать вложения" onChange={(event) => { const selected = [...(event.target.files ?? [])]; event.target.value = ""; if (selected.some((file) => file.size > MAX_FILE_BYTES)) { setError(`Каждый файл должен быть не больше ${MAX_FILE_SIZE_LABEL}`); return; } setFiles((current) => { const known = new Set(current.map((file) => `${file.name}:${file.size}:${file.lastModified}`)); return [...current, ...selected.filter((file) => { const key = `${file.name}:${file.size}:${file.lastModified}`; if (known.has(key)) return false; known.add(key); return true; })]; }); setError(""); }} />{files.length > 0 && <div className="attachment-list">{files.map((file) => <SelectedAttachmentPreview key={`${file.name}:${file.size}:${file.lastModified}`} file={file} onRemove={() => removeSelectedFile(file)} />)}</div>}</>}</div></section>
        {!isDemoMode && <button type="button" className="button button--soft button--wide" disabled={busy} onClick={() => void savePartialDraft()}>{busy ? "Сохраняем…" : sharedDraft ? "Обновить общий черновик" : "Сохранить общий черновик"}</button>}
        {draftNotice && <p className="draft-notice" role="status">{draftNotice}</p>}
        {error && <p className="form-error" role="alert">{error}</p>}
        <button type="submit" className="button button--primary button--wide" disabled={busy}>{busy ? "Проверяем…" : "Продолжить"} <Icon name="arrow" size={19} /></button>
      </form> : <div className="form-stack">
        <section className="panel form-panel" aria-live="polite">
          <h2>Автоматическая проверка</h2>
          {suggestionSource !== "local" ? <>
            <p className="field-help">GigaChat обработал описание и поискал похожие открытые проблемы. Это подсказка, решение остаётся за вами.</p>
            {suggestedTitle && <p className="section-description">Предложенное название: <strong>{suggestedTitle}</strong></p>}
            {descriptionCheck === "warning" ? <p className="form-warning" role="status">Стоит уточнить описание: {descriptionWarning ?? "Укажите, что случилось и где именно."} Вы можете исправить текст или продолжить.</p> : <p className="field-help">Описание достаточно понятно для подачи заявки.</p>}
          </> : <p className="form-warning" role="status">Нейросеть не использовалась. В сводное описание подставлен ваш исходный текст, а похожие карточки найдены по совпадению слов — смысл текста не проверен. При необходимости исправьте формулировку.</p>}
        </section>
        <section className="panel form-panel review-panel"><div className="review-panel__title"><span className="small-icon"><Icon name="check" /></span><div><h2>Проверьте формулировку</h2><p>Её увидят соседи и сотрудники УК.</p></div></div><label className="field"><span>Название проблемы</span><input required value={title} onChange={(event) => setTitle(event.target.value)} maxLength={100} disabled={Boolean(createdIssue)} /></label><label className="field"><span>Сводное и формализованное описание</span><textarea required value={summaryDescription} onChange={(event) => setSummaryDescription(event.target.value)} maxLength={1500} rows={4} disabled={Boolean(createdIssue)} /><small>{summaryDescription.length}/1500 · можно исправить на этом шаге</small></label><div className="summary-list"><span>Категория<strong>{draft?.category}</strong></span><span>Область<strong>{draft && formatIssueScope(draft.scope)}</strong></span><span>Ваш исходный текст<strong>{draft?.description}</strong></span></div></section>
        <section className="panel form-panel"><div className="review-panel__title"><span className="small-icon"><Icon name="search" /></span><div><h2>Возможные совпадения</h2><p>Проверьте похожие открытые проблемы. Если ваша уже есть, поддержите общую карточку.</p></div></div>{similar.length ? <div className="similar-list">{similar.map((item) => <div className="similar-item" key={item.id}><IssueCard compact issue={item} onOpen={() => onOpenIssue(item.id)} /><button type="button" className="button button--soft button--wide" disabled={busy || item.supportedByMe} onClick={() => void support(item.id)}>{item.supportedByMe ? "Вы уже участвуете" : "У меня та же проблема"}</button></div>)}</div> : <p className="muted-text">Похожих проблем не нашлось. Можно создать новую карточку.</p>}</section>
        {files.length > 0 && <section className="panel attachment-panel"><h3>Выбранные вложения</h3><p className="field-help">Они будут добавлены к новой карточке или к вашему описанию при поддержке существующей.</p><div className="attachment-list">{files.map((file) => { const fileId = uploadedFiles.current.get(file); const removable = !fileId || !attachedFiles.current.has(fileId); return <SelectedAttachmentPreview key={`${file.name}:${file.size}:${file.lastModified}`} file={file} onRemove={removable ? () => removeSelectedFile(file) : undefined} />; })}</div></section>}
        {supportedIssueId && <p className="draft-notice">Поддержка уже учтена. Повторное нажатие завершит отправку файлов и черновика без повторной поддержки.</p>}
        {supportedIssueId && <button type="button" className="button button--soft button--wide" onClick={() => onOpenIssue(supportedIssueId)}>Открыть поддержанную карточку</button>}
        {createdIssue && <p className="draft-notice">Карточка уже создана. Повторное нажатие завершит вложения и отметит черновик; вторую карточку мы не создадим.</p>}
        {createdIssue && <button type="button" className="button button--soft button--wide" onClick={() => onOpenIssue(createdIssue.id)}>Открыть созданную карточку</button>}
        {error && <p className="form-error" role="alert">{error}</p>}
        <div className="button-row"><button type="button" className="button button--soft" onClick={() => setStep(1)}>Исправить</button><button type="button" className="button button--primary" disabled={busy || !title.trim() || !summaryDescription.trim()} onClick={() => void create()}>{busy ? "Сохраняем…" : createdIssue ? "Завершить подачу" : "Создать новую"}</button></div>
      </div>}
    </div>
  );
}
