import { useRef, useState } from "react";

import { isDemoMode, issuesClient } from "../features/issues/integrations/client_api";
import { Icon } from "../shared/common_ui/Icon";
import { ScreenHeader } from "../shared/common_ui/ScreenHeader";
import { formatCount } from "../shared/format_count";

interface HouseEntry {
  key: number;
  address: string;
  entranceCount: string;
  apartmentCount: string;
}

export function CompanyRegistration({ onBack, onOpenRequest }: { onBack: () => void; onOpenRequest: (id: string) => void }) {
  const [companyName, setCompanyName] = useState("");
  const [firstStaffPhone, setFirstStaffPhone] = useState("");
  const [explanation, setExplanation] = useState("");
  const [houseEntries, setHouseEntries] = useState<HouseEntry[]>([{ key: 0, address: "", entranceCount: "", apartmentCount: "" }]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [created, setCreated] = useState<{ companyId: string; houseCount: number } | null>(null);
  const companyRequestId = useRef<string | null>(null);
  const submittedAddresses = useRef(new Set<string>());
  const nextHouseKey = useRef(1);

  function changeHouse(key: number, field: "address" | "entranceCount" | "apartmentCount", value: string) {
    setHouseEntries((entries) => entries.map((item) => item.key === key ? { ...item, [field]: value } : item));
  }

  function addHouse() {
    const key = nextHouseKey.current++;
    setHouseEntries((entries) => [...entries, { key, address: "", entranceCount: "", apartmentCount: "" }]);
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true); setError("");
    try {
      const houses = houseEntries.filter((item) => item.address.trim() || item.entranceCount || item.apartmentCount);
      const addresses = new Set<string>();
      for (const house of houses) {
        const address = house.address.trim();
        const entranceCount = Number(house.entranceCount);
        const apartmentCount = Number(house.apartmentCount);
        if (!address || !Number.isSafeInteger(entranceCount) || entranceCount < 1 || !Number.isSafeInteger(apartmentCount) || apartmentCount < 1) {
          throw new Error("Для каждого дома укажите адрес и положительное количество подъездов и квартир");
        }
        if (addresses.has(address)) throw new Error(`Дом «${address}» указан дважды`);
        addresses.add(address);
      }
      if (!companyRequestId.current) {
        const company = await issuesClient.createCompanyRegistration({ companyName, firstStaffPhone, explanation });
        companyRequestId.current = company.id;
      }
      for (const house of houses) {
        const address = house.address.trim();
        if (submittedAddresses.current.has(address)) continue;
        await issuesClient.createHouseRequest(address, Number(house.entranceCount), Number(house.apartmentCount), companyRequestId.current);
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
      {created ? <section className="panel success-panel"><span className="success-icon"><Icon name="check" size={30} /></span><h2>{isDemoMode ? "Пробная заявка сохранена" : "Заявка отправлена поддержке"}</h2><p>На подключение домов — {formatCount(created.houseCount, ["заявка", "заявки", "заявок"])}. {isDemoMode ? "Статус пробной заявки можно посмотреть в списке заявок." : "Статус проверки и переписка с поддержкой доступны в заявке. После проверки УК поддержка выдаст первому сотруднику личный доступ по номеру телефона."}</p><small>Номер заявки: {created.companyId.slice(0, 8)}</small><button type="button" className="button button--primary" onClick={() => onOpenRequest(created.companyId)}>{isDemoMode ? "Открыть заявку" : "Открыть заявку и обсуждение"}</button><button type="button" className="button button--soft" onClick={onBack}>На главную</button></section> : <form className="form-stack" onSubmit={(event) => void submit(event)}>
        <section className="panel form-panel"><h2>Управляющая компания</h2><label className="field"><span>Название УК</span><input required value={companyName} onChange={(event) => setCompanyName(event.target.value)} placeholder="Например: УК «Дом-Сервис»" /></label><label className="field"><span>Номер первого сотрудника</span><input required type="tel" inputMode="tel" value={firstStaffPhone} onChange={(event) => setFirstStaffPhone(event.target.value)} placeholder="+7 999 123-45-67" /></label><p className="field-help">Можно указать номер другого человека. Общий пароль сотрудникам не выдаётся.</p><label className="field"><span>Пояснение для поддержки</span><textarea required rows={4} value={explanation} onChange={(event) => setExplanation(event.target.value)} placeholder="Кто обращается, как с вами связаться и какие дома обслуживает УК" /></label></section>
        <section className="panel form-panel"><h2>Дома для подключения</h2><p className="section-description">Каждый дом станет отдельной заявкой на проверку. Для каждого укажите количество подъездов и квартир.</p>{houseEntries.map((house, index) => <div className="form-stack" key={house.key}><h3>Дом {index + 1}</h3><label className="field"><span>Адрес</span><input value={house.address} onChange={(event) => changeHouse(house.key, "address", event.target.value)} placeholder="ул. Пушкина, д. 5" /></label><div className="field-grid"><label className="field"><span>Количество подъездов</span><input type="number" min="1" inputMode="numeric" required={Boolean(house.address.trim())} value={house.entranceCount} onChange={(event) => changeHouse(house.key, "entranceCount", event.target.value)} /></label><label className="field"><span>Количество квартир</span><input type="number" min="1" inputMode="numeric" required={Boolean(house.address.trim())} value={house.apartmentCount} onChange={(event) => changeHouse(house.key, "apartmentCount", event.target.value)} /></label></div>{houseEntries.length > 1 && <button type="button" className="button button--soft" onClick={() => setHouseEntries((entries) => entries.filter((item) => item.key !== house.key))}>Убрать дом</button>}</div>)}<button type="button" className="button button--soft" onClick={addHouse}>Добавить ещё дом</button><p className="field-help">Дома можно добавить и позже, после подключения УК.</p></section>
        {error && <p className="form-error" role="alert">{error}</p>}
        <button type="submit" className="button button--primary button--wide" disabled={busy}>{busy ? "Отправляем…" : isDemoMode ? "Сохранить пробную заявку" : "Отправить заявку"}</button>
      </form>}
    </div>
  );
}
