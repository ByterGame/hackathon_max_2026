import { formatDate, type ResidentOffer, type ResidentRequest } from "../features/issues/types";
import { NotificationToggle } from "../features/notifications/ui/NotificationToggle";
import { Icon } from "../shared/common_ui/Icon";
import { ScreenHeader } from "../shared/common_ui/ScreenHeader";

export function OnboardingHome({ requests, offers, onAccess, onRegister }: { requests: ResidentRequest[]; offers: ResidentOffer[]; onAccess: () => void; onRegister: () => void }) {
  return (
    <div className="page page--onboarding">
      <ScreenHeader title="Подключить дом" subtitle="Доступ к обращениям" icon="home" />
      <section className="panel onboarding-hero"><span className="small-icon"><Icon name="key" size={23} /></span><h2>Получите доступ к своему дому</h2><p>Найдите подключённый дом, укажите подъезд и квартиру. Заявку рассмотрит управляющая компания. До её решения проблемы дома недоступны.</p><button type="button" className="button button--primary button--wide" onClick={onAccess}>Подать заявку на доступ</button></section>
      {offers.some((item) => item.status === "pending") && <button className="request-hint" type="button" onClick={onAccess}><Icon name="bell" size={18} /> У вас есть предложение доступа от УК <Icon name="chevron" size={17} /></button>}
      {requests.length > 0 && <section className="requests-list"><div className="section-heading"><h2>Мои заявки</h2></div>{requests.map((request) => <div className="panel request-card" key={request.id}><strong>{request.address ?? "Дом по заявке"}</strong><span>Подъезд №{request.entrance}, кв. {request.apartment}</span><small>{formatDate(request.createdAt)} · {request.status === "closed" ? request.outcome === "granted" ? "Доступ выдан" : "Отказано" : "Открыта"}</small><NotificationToggle subject="resident_request" id={request.id} /></div>)}</section>}
      <section className="panel onboarding-company"><span className="small-icon"><Icon name="building" /></span><div><h3>Представляете управляющую компанию?</h3><p>Отправьте обращение на подключение. Поддержка проверит компанию и выдаст первый личный доступ.</p><button type="button" className="button button--soft" onClick={onRegister}>Подключить УК</button></div></section>
    </div>
  );
}
