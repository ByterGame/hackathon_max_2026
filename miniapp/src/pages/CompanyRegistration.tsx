import { useRef, useState } from "react";

import { isDemoMode, issuesClient } from "../features/issues/integrations/client_api";
import { Icon } from "../shared/common_ui/Icon";
import { ScreenHeader } from "../shared/common_ui/ScreenHeader";

export function CompanyRegistration({ onBack }: { onBack: () => void }) {
  const [companyName, setCompanyName] = useState("");
  const [firstStaffPhone, setFirstStaffPhone] = useState("");
  const [explanation, setExplanation] = useState("");
  const [addresses, setAddresses] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [created, setCreated] = useState<{ companyId: string; houseCount: number } | null>(null);
  const companyRequestId = useRef<string | null>(null);
  const submittedAddresses = useRef(new Set<string>());

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true); setError("");
    try {
      if (!companyRequestId.current) {
        const company = await issuesClient.createCompanyRegistration({ companyName, firstStaffPhone, explanation });
        companyRequestId.current = company.id;
      }
      const houseAddresses = addresses.split("\n").map((item) => item.trim()).filter(Boolean);
      for (const address of houseAddresses) {
        if (submittedAddresses.current.has(address)) continue;
        await issuesClient.createHouseRequest(address, companyRequestId.current);
        submittedAddresses.current.add(address);
      }
      setCreated({ companyId: companyRequestId.current, houseCount: submittedAddresses.current.size });
    } catch (reason) {
      const detail = reason instanceof Error ? reason.message : "Не удалось подать заявку";
      setError(companyRequestId.current ? `Обращение УК уже создано. ${detail}. Повторная отправка продолжит заявки на дома без дублирования УК.` : detail);
    }
    finally { setBusy(false); }
  }

  return (
    <div className="page page--form">
      <ScreenHeader title="Подключить УК" subtitle="Обращение к поддержке" onBack={onBack} />
      <div className="info-panel"><Icon name="info" size={20} /> {isDemoMode ? "Заявка сохраняется только в этом браузере и не отправляется поддержке." : "Поддержка проверит компанию и каждый дом отдельно, затем выдаст первый персональный доступ."}</div>
      {created ? <section className="panel success-panel"><span className="success-icon"><Icon name="check" size={30} /></span><h2>{isDemoMode ? "Пробная заявка сохранена" : "Заявка отправлена поддержке"}</h2><p>Отдельных заявок на дома: {created.houseCount}. После проверки УК поддержка выдаст первому сотруднику личный доступ по номеру телефона.</p><small>Номер заявки: {created.companyId.slice(0, 8)}</small><button type="button" className="button button--primary" onClick={onBack}>На главную</button></section> : <form className="form-stack" onSubmit={(event) => void submit(event)}>
        <section className="panel form-panel"><h2>Управляющая компания</h2><label className="field"><span>Название УК</span><input required value={companyName} onChange={(event) => setCompanyName(event.target.value)} placeholder="Например: УК «Дом-Сервис»" /></label><label className="field"><span>Номер первого сотрудника</span><input required type="tel" inputMode="tel" value={firstStaffPhone} onChange={(event) => setFirstStaffPhone(event.target.value)} placeholder="+7 999 123-45-67" /></label><p className="field-help">Можно указать номер другого человека. Общий пароль сотрудникам не выдаётся.</p><label className="field"><span>Пояснение для поддержки</span><textarea required rows={4} value={explanation} onChange={(event) => setExplanation(event.target.value)} placeholder="Кто обращается, как с вами связаться и какие дома обслуживает УК" /></label></section>
        <section className="panel form-panel"><h2>Дома для подключения</h2><p className="section-description">Каждый адрес станет отдельной заявкой на проверку.</p><label className="field"><span>Адреса, каждый с новой строки</span><textarea rows={4} value={addresses} onChange={(event) => setAddresses(event.target.value)} placeholder="ул. Пушкина, д. 5\nул. Ленина, д. 12" /></label><p className="field-help">Дома можно добавить и позже, после подключения УК.</p></section>
        {error && <p className="form-error" role="alert">{error}</p>}
        <button type="submit" className="button button--primary button--wide" disabled={busy}>{busy ? "Отправляем…" : isDemoMode ? "Сохранить пробную заявку" : "Отправить заявку"}</button>
      </form>}
    </div>
  );
}
