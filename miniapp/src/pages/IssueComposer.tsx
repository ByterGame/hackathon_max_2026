import { useEffect, useRef, useState } from "react";

import { getDraft, listDrafts, saveDraft, submitDraft, type DraftData } from "../features/drafts/integrations/client_api";
import { SelectedAttachmentPreview } from "../features/files/AttachmentPreview";
import { MAX_FILE_BYTES, MAX_FILE_SIZE_LABEL, SUPPORTED_FILE_FORMATS } from "../features/files/file_rules";
import { attachFile, listFiles, uploadFile } from "../features/files/integrations/client_api";
import { isDemoMode, issuesClient } from "../features/issues/integrations/client_api";
import { formatIssueScope, type CreateIssueInput, type House, type Issue, type IssueCategory, type IssueScopeLevel, type ResidentGrant } from "../features/issues/types";
import { IssueCard } from "../features/issues/ui/IssueCard";
import { Icon } from "../shared/common_ui/Icon";
import { ScreenHeader } from "../shared/common_ui/ScreenHeader";
import "./issue-composer.css";

type SubmissionTarget = { kind: "create" } | { kind: "support"; issueId: string };

export function IssueComposer({ house, grants, categories, onBack, onOpenIssue }: { house: House; grants: ResidentGrant[]; categories: IssueCategory[]; onBack: () => void; onOpenIssue: (id: string) => void }) {
  const [step, setStep] = useState<1 | 2>(1);
  const [category, setCategory] = useState("");
  const [description, setDescription] = useState("");
  const [summaryDescription, setSummaryDescription] = useState("");
  const [title, setTitle] = useState("");
  const [scopeLevel, setScopeLevel] = useState<IssueScopeLevel | null>(null);
  const [selectedApartmentId, setSelectedApartmentId] = useState("");
  const [selectedEntrance, setSelectedEntrance] = useState("");
  const [similar, setSimilar] = useState<Issue[]>([]);
  const [suggestionSource, setSuggestionSource] = useState<"gigachat" | "local" | null>(null);
  const [suggestedTitle, setSuggestedTitle] = useState("");
  const [descriptionCheck, setDescriptionCheck] = useState<"ok" | "warning" | "not_checked">("not_checked");
  const [descriptionWarning, setDescriptionWarning] = useState<string | null>(null);
  const [summaryFallback, setSummaryFallback] = useState(false);
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
  const [submissionTarget, setSubmissionTarget] = useState<SubmissionTarget | null>(null);
  const submissionTargetRef = useRef<SubmissionTarget | null>(null);
  const submissionBusyRef = useRef(false);
  const editedByUser = useRef(false);
  const manuallyEditedTitleForDescription = useRef<string | null>(null);
  const manuallyEditedSummaryForDescription = useRef<string | null>(null);
  const entrances = [...new Set(grants.flatMap((item) => item.entrance == null ? [] : [item.entrance]))].sort((left, right) => left - right);
  const entrance = entrances.length === 1 ? entrances[0] : Number(selectedEntrance);
  const ownGrant = scopeLevel === "apartment"
    ? grants.length === 1 ? grants[0] : grants.find((item) => item.apartmentId === selectedApartmentId)
    : scopeLevel === "entrance"
      ? grants.find((item) => item.entrance === entrance)
      : undefined;
  const imageFileCount = files.filter((file) => file.type.startsWith("image/")).length;
  const videoFileCount = files.filter((file) => file.type.startsWith("video/")).length;

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

  function addFiles(event: React.ChangeEvent<HTMLInputElement>) {
    const selected = [...(event.target.files ?? [])];
    event.target.value = "";
    if (selected.some((file) => file.size > MAX_FILE_BYTES)) {
      setError(`Каждый файл должен быть не больше ${MAX_FILE_SIZE_LABEL}`);
      return;
    }
    setFiles((current) => {
      const known = new Set(current.map((file) => `${file.name}:${file.size}:${file.lastModified}`));
      return [...current, ...selected.filter((file) => {
        const key = `${file.name}:${file.size}:${file.lastModified}`;
        if (known.has(key)) return false;
        known.add(key);
        return true;
      })];
    });
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
      const savedScope = payload.scope;
      setScopeLevel(savedScope === "apartment" || savedScope === "entrance" || savedScope === "house" ? savedScope : payload.scope_all_house === true ? "house" : null);
      const savedGrant = typeof payload.apartment_id === "string" ? grants.find((item) => item.apartmentId === payload.apartment_id) : undefined;
      setSelectedApartmentId(savedGrant?.apartmentId ?? "");
      setSelectedEntrance(savedGrant?.entrance?.toString() ?? "");
      setDraftNotice(savedScope === undefined && payload.scope_all_house !== true
        ? "Черновик восстановлен. Раньше в нём можно было указать произвольные квартиры; выберите область проблемы заново."
        : "Восстановлен общий черновик MAX и бота.");
    }).catch(() => { if (active) setDraftNotice("Не удалось загрузить общий черновик. Можно продолжить без него."); });
    return () => { active = false; };
  }, [house.id, categories, grants]);

  function draftPayload(): Record<string, unknown> {
    const categoryId = categories.find((item) => item.name === category)?.id;
    return {
      house_id: house.id,
      ...(categoryId ? { category_id: categoryId } : {}),
      ...(title.trim() ? { title: title.trim() } : {}),
      ...(description.trim() ? { description: description.trim() } : {}),
      ...(summaryDescription.trim() ? { summary_description: summaryDescription.trim() } : {}),
      ...(scopeLevel ? { scope: scopeLevel } : {}),
      ...(scopeLevel !== "house" && ownGrant?.apartmentId ? { apartment_id: ownGrant.apartmentId } : {}),
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
    if (!category) { setError("Выберите категорию проблемы"); return; }
    if (!scopeLevel) { setError("Выберите, где возникла проблема: в квартире, подъезде или доме"); return; }
    if (scopeLevel === "apartment" && !ownGrant) { setError("Выберите свою квартиру из списка подтверждённых"); return; }
    if (scopeLevel === "entrance" && entrances.length === 0) { setError("В вашем доступе не указан подъезд. Уточните его у УК."); return; }
    if (scopeLevel === "entrance" && !ownGrant) { setError("Выберите свой подъезд из списка подтверждённых"); return; }
    const normalizedDescription = description.trim();
    const manualTitle = manuallyEditedTitleForDescription.current === normalizedDescription ? title.trim() : "";
    const proposedTitle = manualTitle || normalizedDescription.split(/[.!?\n]/)[0].slice(0, 100);
    if (!proposedTitle) { setError("Опишите проблему"); return; }
    const input: CreateIssueInput = {
      houseId: house.id,
      title: proposedTitle,
      category,
      description: normalizedDescription,
      scopeLevel,
      apartmentId: scopeLevel === "house" ? undefined : ownGrant?.apartmentId,
      scope: scopeLevel === "house"
        ? { allHouse: true, entrances: [], apartments: [] }
        : scopeLevel === "entrance"
          ? { allHouse: false, entrances: [ownGrant!.entrance!], apartments: [] }
          : { allHouse: false, entrances: [], apartments: [{ number: ownGrant!.apartment, entrance: ownGrant!.entrance }] },
    };
    setDraft(input);
    setBusy(true);
    setError("");
    try {
      const suggestion = await issuesClient.suggestIssue(input);
      const suggestedSummary = suggestion.summaryDescription?.trim() ?? "";
      const usableSuggestedSummary = suggestedSummary.length >= 8 && suggestedSummary.split(/\s+/).length >= 2;
      const manualSummary = manuallyEditedSummaryForDescription.current === normalizedDescription ? summaryDescription.trim() : "";
      const nextSummary = manualSummary || (usableSuggestedSummary ? suggestedSummary : input.description);
      const nextTitle = manualTitle || suggestion.suggestedTitle.trim() || proposedTitle;
      setSimilar(suggestion.similarIssues);
      setSuggestionSource(suggestion.source);
      setSuggestedTitle(suggestion.suggestedTitle);
      setSummaryDescription(nextSummary);
      setSummaryFallback(suggestion.source === "gigachat" && !manualSummary && !usableSuggestedSummary);
      setDescriptionCheck(suggestion.descriptionCheck);
      setDescriptionWarning(suggestion.descriptionWarning);
      setTitle(nextTitle);
      if (!isDemoMode) {
        try { await persistDraft({ ...draftPayload(), title: nextTitle, summary_description: nextSummary }); }
        catch (reason) { setDraftNotice(`Черновик не сохранён: ${reason instanceof Error ? reason.message : "ошибка сервера"}`); }
      }
      setStep(2);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Не удалось проверить похожие проблемы"); }
    finally { setBusy(false); }
  }

  async function create() {
    if (submissionBusyRef.current) return;
    if (submissionTargetRef.current?.kind === "support" || supportedIssueId) {
      setError("Поддержка другой карточки уже начата. Новую проблему из этого черновика создавать нельзя.");
      return;
    }
    if (!draft) return;
    if (!summaryDescription.trim()) { setError("Проверьте сводное описание проблемы"); return; }
    if (summaryDescription.trim().length > 1500) { setError("Сводное описание не должно превышать 1500 символов"); return; }
    submissionBusyRef.current = true;
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
      let issue = createdIssue;
      if (!issue) {
        if (!submissionTargetRef.current) {
          const target: SubmissionTarget = { kind: "create" };
          submissionTargetRef.current = target;
          setSubmissionTarget(target);
        }
        issue = await issuesClient.createIssue({ ...draft, title: title.trim(), summaryDescription: summaryDescription.trim() }, createKey.current ?? undefined);
      }
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
    finally { submissionBusyRef.current = false; setBusy(false); }
  }

  async function support(issueId: string) {
    if (submissionBusyRef.current) return;
    if (submissionTargetRef.current?.kind === "create" || createdIssue) {
      setError("Создание новой карточки уже начато. Поддержать другую из этого черновика нельзя.");
      return;
    }
    if (submissionTargetRef.current?.kind === "support" && submissionTargetRef.current.issueId !== issueId) {
      setError("Поддержка другой карточки уже начата. Завершите её или откройте карточку.");
      return;
    }
    if (!draft) return;
    submissionBusyRef.current = true;
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
        if (!submissionTargetRef.current) {
          const target: SubmissionTarget = { kind: "support", issueId };
          submissionTargetRef.current = target;
          setSubmissionTarget(target);
        }
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
    finally { submissionBusyRef.current = false; setBusy(false); }
  }

  return (
    <div className="page page--form issue-composer">
      <ScreenHeader title="Сообщить о проблеме" subtitle={step === 1 ? "Шаг 1 из 2 · Описание" : "Шаг 2 из 2 · Проверка"} onBack={step === 2 && !submissionTarget ? () => setStep(1) : onBack} />
      {step === 1 && <div className="issue-composer__steps" aria-label="Шаги подачи проблемы"><span className="is-active" aria-current="step">1 Описание</span><span>2 Проверка</span></div>}

      {step === 1 ? <form className="form-stack issue-composer__form" onInputCapture={() => { editedByUser.current = true; }} onSubmit={(event) => void review(event)}>
        <section className="issue-composer__section" aria-labelledby="issue-category-title">
          <div className="issue-composer__section-heading"><h2 id="issue-category-title">Категория</h2><p className="issue-composer__context" title={house.address}>{house.address}</p></div>
          <div className="issue-composer__chips" role="group" aria-label="Категория проблемы">
            {categories.map((item) => <button key={item.id} type="button" className={`issue-composer__chip${category === item.name ? " issue-composer__chip--selected" : ""}`} aria-pressed={category === item.name} onClick={() => { editedByUser.current = true; setCategory(item.name); setError(""); }}>{item.name}</button>)}
          </div>
        </section>
        <section className="issue-composer__section" aria-labelledby="issue-description-title">
          <h2 id="issue-description-title">Опишите проблему</h2>
          <label className="field issue-composer__description"><span className="issue-composer__sr-only">Описание проблемы</span><textarea required maxLength={1500} value={description} onChange={(event) => { setDescription(event.target.value); setTitle(""); setSummaryDescription(""); manuallyEditedTitleForDescription.current = null; manuallyEditedSummaryForDescription.current = null; setSummaryFallback(false); }} placeholder="Например: лифт в подъезде №2 не реагирует на вызов..." rows={5} /><small>{description.length}/1500</small></label>
        </section>
        <section className="issue-composer__section" aria-labelledby="issue-scope-title">
          <h2 id="issue-scope-title">Область</h2>
          <div className="issue-composer__chips issue-composer__chips--scope" role="group" aria-label="Область проблемы">
            {([
              ["entrance", "Подъезд", "Видна соседям по подъезду и УК"],
              ["house", "Дом", "Видна всем жильцам дома и УК"],
              ["apartment", "Квартира", "Видна только вам и УК"],
            ] as const).map(([value, label, hint]) => <button key={value} type="button" className={`issue-composer__chip${scopeLevel === value ? " issue-composer__chip--selected" : ""}`} aria-label={`${label}: ${hint}`} title={hint} aria-pressed={scopeLevel === value} onClick={() => { editedByUser.current = true; setScopeLevel(value); setError(""); }}>{label}</button>)}
          </div>
          {scopeLevel === "apartment" && <p className="field-help">Эту проблему увидите только вы и УК.</p>}
          {scopeLevel === "apartment" && grants.length > 1 && <label className="field"><span>Какая из ваших квартир?</span><select required value={selectedApartmentId} onChange={(event) => { editedByUser.current = true; setSelectedApartmentId(event.target.value); }}><option value="">Выберите подтверждённую квартиру</option>{grants.map((item) => <option key={item.id} value={item.apartmentId ?? ""}>Квартира {item.apartment}{item.entrance ? ` · подъезд ${item.entrance}` : ""}</option>)}</select></label>}
          {scopeLevel === "entrance" && entrances.length > 1 && <label className="field"><span>В каком из ваших подъездов?</span><select required value={selectedEntrance} onChange={(event) => { editedByUser.current = true; setSelectedEntrance(event.target.value); }}><option value="">Выберите подтверждённый подъезд</option>{entrances.map((item) => <option key={item} value={item}>Подъезд №{item}</option>)}</select></label>}
          {scopeLevel === "entrance" && entrances.length === 0 && <p className="form-warning">Подъезд не указан в вашем доступе. Обратитесь в УК или выберите область «весь дом».</p>}
        </section>
        <section className="issue-composer__section issue-composer__media" aria-labelledby="issue-media-title">
          <h2 id="issue-media-title">Фото, видео и материалы</h2>
          <div className="issue-composer__media-grid">
            <span className="issue-composer__media-tile">Фото{imageFileCount > 0 && <small>{imageFileCount}</small>}</span>
            <span className="issue-composer__media-tile">Видео{videoFileCount > 0 && <small>{videoFileCount}</small>}</span>
            {isDemoMode
              ? <button type="button" className="issue-composer__media-tile issue-composer__media-tile--add" onClick={() => setDraftNotice("В демонстрационном режиме вложения не отправляются.")}>+ Добавить</button>
              : <label className="issue-composer__media-tile issue-composer__media-tile--add">+ Добавить<input ref={fileInput} type="file" multiple accept="image/jpeg,image/png,image/webp,application/pdf,video/mp4,video/quicktime" aria-label="Выбрать вложения" onChange={addFiles} /></label>}
          </div>
          {!isDemoMode && <details className="issue-composer__media-help"><summary>Форматы и размер файлов</summary><p>Форматы: {SUPPORTED_FILE_FORMATS} · до {MAX_FILE_SIZE_LABEL} на файл. Доступ сохраняется вместе с проблемой.</p></details>}
          {files.length > 0 && <div className="attachment-list">{files.map((file) => <SelectedAttachmentPreview key={file.name + ":" + file.size + ":" + file.lastModified} file={file} onRemove={() => removeSelectedFile(file)} />)}</div>}
        </section>
        {draftNotice && <p className="draft-notice" role="status">{draftNotice}</p>}
        {error && <p className="form-error" role="alert">{error}</p>}
        <button type="submit" className="button button--primary button--wide issue-composer__continue" disabled={busy}>{busy ? "Проверяем…" : "Продолжить"}</button>
        {!isDemoMode && <button type="button" className="button button--soft button--wide issue-composer__save" disabled={busy} onClick={() => void savePartialDraft()}>{busy ? "Сохраняем…" : sharedDraft ? "Обновить общий черновик" : "Сохранить общий черновик"}</button>}
      </form> : <div className="form-stack issue-composer__check">
        <section className="issue-composer__summary" aria-live="polite">
          <h2>Мы поняли проблему так</h2>
          <p className="issue-composer__summary-row">Название · {title}</p>
          <p className="issue-composer__summary-row">Категория · {draft?.category}</p>
          <p className="issue-composer__summary-row">Область · {draft && formatIssueScope(draft.scope)}</p>
          <p className="issue-composer__summary-text">{summaryDescription}</p>
          {!submissionTarget && <details className="issue-composer__optional issue-composer__edit-summary">
            <summary>Изменить название и сводку</summary>
            <label className="field"><span>Название проблемы</span><input required value={title} onChange={(event) => { setTitle(event.target.value); manuallyEditedTitleForDescription.current = draft?.description ?? null; }} maxLength={100} /></label>
            <label className="field"><span>Сводное описание</span><textarea required value={summaryDescription} onChange={(event) => { setSummaryDescription(event.target.value); manuallyEditedSummaryForDescription.current = draft?.description ?? null; }} maxLength={1500} rows={4} /><small>{summaryDescription.length}/1500</small></label>
            <p className="field-help">Ваш исходный текст: {draft?.description}</p>
          </details>}
        </section>
        {suggestionSource === "local"
          ? <aside className="form-warning issue-composer__check-note" role="status">Нейросеть не использовалась. В сводное описание подставлен ваш исходный текст, а похожие карточки найдены по совпадению слов — смысл текста не проверен. При необходимости исправьте формулировку.</aside>
          : descriptionCheck === "warning" && <aside className="form-warning issue-composer__check-note" role="status">{descriptionWarning ?? "Укажите, что случилось и где именно."} Вы можете исправить текст или продолжить.</aside>}
        {summaryFallback && <aside className="form-warning issue-composer__check-note" role="status">Сводка GigaChat оказалась слишком короткой и не использована. Вместо неё показан ваш исходный текст — при необходимости исправьте его.</aside>}
        {error && <p className="form-error" role="alert">{error}</p>}
        {submissionTarget && <p className="draft-notice issue-composer__submission-note" role="status">{submissionTarget.kind === "create"
          ? createdIssue ? "Карточка уже создана. Если вложения или черновик не завершились, повторите подачу; поддержка другой карточки здесь недоступна." : "Создание уже отправлено. Если ответ не пришёл, повторите подачу: тот же запрос не создаст вторую карточку."
          : supportedIssueId ? "Поддержка уже учтена. Если вложения или черновик не завершились, повторите поддержку; новую карточку здесь создавать нельзя." : "Поддержка уже отправлена. Если ответ не пришёл, повторите её для той же карточки."}</p>}
        <div className="issue-composer__decision-actions">
          <button type="button" className="button button--soft" disabled={busy || Boolean(submissionTarget)} onClick={() => setStep(1)}>Исправить</button>
          <button type="button" className="button button--primary" disabled={busy || !title.trim() || !summaryDescription.trim() || submissionTarget?.kind === "support"} onClick={() => void create()}>{busy ? "Сохраняем…" : createdIssue ? "Завершить подачу" : submissionTarget?.kind === "create" ? "Повторить подачу" : "Создать заявку"}</button>
        </div>
        {similar.length
          ? <div className="issue-composer__similar-list">{similar.map((item, index) => <div className="issue-composer__similar-match" key={item.id}>
            <section className="issue-composer__similar issue-composer__similar--matches">
              <h2>{index === 0 ? "Похоже, об этой проблеме уже сообщили" : "Ещё похожая проблема"}</h2>
              <IssueCard issue={item} onOpen={() => onOpenIssue(item.id)} />
            </section>
            <button type="button" className="button button--primary button--wide issue-composer__support" disabled={busy || item.supportedByMe || submissionTarget?.kind === "create" || (submissionTarget?.kind === "support" && submissionTarget.issueId !== item.id)} onClick={() => void support(item.id)}><Icon name="people" size={20} />{item.supportedByMe ? "Вы уже участвуете" : supportedIssueId === item.id ? "Завершить поддержку" : submissionTarget?.kind === "support" && submissionTarget.issueId === item.id ? "Повторить поддержку" : "У меня та же проблема"}</button>
          </div>)}</div>
          : <section className="issue-composer__similar"><h2>Похожих проблем не нашли</h2><p className="muted-text">Можно создать новую карточку.</p></section>}
        {suggestionSource === "gigachat" && <details className="issue-composer__analysis">
          <summary>Как проверяли описание</summary>
          <p>GigaChat обработал описание и поискал похожие открытые проблемы. Это подсказка, решение остаётся за вами.</p>
          {suggestedTitle && <p>Предложенное название: <strong>{suggestedTitle}</strong></p>}
        </details>}
        {files.length > 0 && <section className="panel attachment-panel"><h3>Выбранные вложения</h3><p className="field-help">Они будут добавлены к новой карточке или к вашему описанию при поддержке существующей.</p><div className="attachment-list">{files.map((file) => { const fileId = uploadedFiles.current.get(file); const removable = !fileId || !attachedFiles.current.has(fileId); return <SelectedAttachmentPreview key={`${file.name}:${file.size}:${file.lastModified}`} file={file} onRemove={removable ? () => removeSelectedFile(file) : undefined} />; })}</div></section>}
        {supportedIssueId && <button type="button" className="button button--soft button--wide" onClick={() => onOpenIssue(supportedIssueId)}>Открыть поддержанную карточку</button>}
        {createdIssue && <button type="button" className="button button--soft button--wide" onClick={() => onOpenIssue(createdIssue.id)}>Открыть созданную карточку</button>}
      </div>}
    </div>
  );
}
