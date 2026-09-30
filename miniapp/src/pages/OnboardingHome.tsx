import { type CompanyRegistrationRequest, type HouseAdditionRequest, type ResidentOffer, type ResidentRequest } from "../features/issues/types";
import { Icon } from "../shared/common_ui/Icon";
import { ScreenHeader } from "../shared/common_ui/ScreenHeader";
import { ApplicantRequestsList, type ApplicantCase } from "./MyRequests";

export function OnboardingHome({ requests, companyRequests, houseRequests, offers, onAccess, onRegister, onRequest, onAllRequests, onNotifications, unreadCount }: { requests: ResidentRequest[]; companyRequests: CompanyRegistrationRequest[]; houseRequests: HouseAdditionRequest[]; offers: ResidentOffer[]; onAccess: () => void; onRegister: () => void; onRequest: (request: ApplicantCase) => void; onAllRequests: () => void; onNotifications: () => void; unreadCount: number }) {
  return (
    <div className="page page--onboarding">
      <ScreenHeader title="Подключить дом" subtitle="Доступ к обращениям" icon="home" action={{ label: "Уведомления", onClick: onNotifications, icon: "bell", badge: unreadCount }} />
      <section className="panel onboarding-hero"><span className="small-icon"><Icon name="key" size={23} /></span><h2>Получите доступ к своему дому</h2><p>Найдите подключённый дом и укажите номер квартиры. Заявку рассмотрит управляющая компания. До её решения проблемы дома недоступны.</p><button type="button" className="button button--primary button--wide" onClick={onAccess}>Подать заявку на доступ</button></section>
      {offers.some((item) => item.status === "pending") && <button className="request-hint" type="button" onClick={onAccess}><Icon name="bell" size={18} /> У вас есть предложение доступа от УК <Icon name="chevron" size={17} /></button>}
      <ApplicantRequestsList residentRequests={requests} companyRequests={companyRequests} houseRequests={houseRequests} onOpen={onRequest} limit={3} />
      {requests.length + companyRequests.length + houseRequests.length > 3 && <button type="button" className="text-link" onClick={onAllRequests}>Все заявки <Icon name="chevron" size={17} /></button>}
      <section className="panel onboarding-company"><span className="small-icon"><Icon name="building" /></span><div><h3>Представляете управляющую компанию?</h3><p>Отправьте обращение на подключение. Поддержка проверит компанию и выдаст первый личный доступ.</p><button type="button" className="button button--soft" onClick={onRegister}>Подключить УК</button></div></section>
    </div>
  );
}
