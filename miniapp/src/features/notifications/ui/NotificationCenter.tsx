import { formatDate } from "../../issues/types";
import type { AppSnapshot } from "../../issues/integrations/client_api";
import type { AppNotification, NotificationAudience } from "../integrations/client_api";
import { Icon } from "../../../shared/common_ui/Icon";
import { ScreenHeader } from "../../../shared/common_ui/ScreenHeader";
import "./notification-center.css";

export type NotificationDestination = "issue" | "request" | "access" | "home" | "employee-access" | "support-request" | "admin-record" | null;

export function notificationDestination(item: AppNotification, role: NotificationAudience): NotificationDestination {
  if (role === "support") return ["company_registration_request", "house_addition_request"].includes(item.subject_kind) ? "support-request" : null;
  if (role === "admin") return ["issue_card", "resident_request", "company_registration_request", "house_addition_request", "resident_offer", "resident_grant", "staff_assignment"].includes(item.subject_kind) ? "admin-record" : null;
  if (item.subject_kind === "issue_card") return "issue";
  if (["resident_request", "company_registration_request", "house_addition_request"].includes(item.subject_kind)) return "request";
  if (item.subject_kind === "resident_offer") return role === "resident" ? "access" : "employee-access";
  if (item.subject_kind === "resident_grant") return role === "resident" ? "home" : "employee-access";
  if (item.subject_kind === "staff_assignment") return role === "employee" ? "employee-access" : null;
  return null;
}

function eventTitle(item: AppNotification): string {
  const action = item.event_kind.split(".").at(-1);
  if (item.subject_kind === "issue_card") {
    switch (action) {
      case "created": return "Новая проблема дома";
      case "status_changed": return "Изменён статус проблемы";
      case "comment_added": return "Новый ответ по проблеме";
      case "report_added": return "Проблему дополнили";
      case "supported": return "Проблему поддержали";
      case "reopened": return "Проблема переоткрыта";
      case "merged": return "Проблемы объединены";
      default: return "Проблема обновлена";
    }
  }
  if (action === "message_added") return "Новое сообщение в заявке";
  if (action === "status_changed") return "Изменён статус заявки";
  if (action === "closed") return "По заявке принято решение";
  if (action === "cancellation_requested") return "Запрошена отмена заявки";
  if (action === "cancellation_accepted") return "Заявка отменена";
  if (action === "cancellation_rejected") return "Отмена заявки отклонена";
  if (item.subject_kind === "resident_offer") return action === "accepted" ? "Предложение доступа принято" : action === "declined" ? "Предложение доступа отклонено" : "Предложение доступа к дому";
  if (item.subject_kind === "resident_grant") return action === "revoke" ? "Доступ к дому отозван" : "Доступ к дому изменён";
  if (item.subject_kind === "staff_assignment") return action === "revoked" ? "Права сотрудника отозваны" : "Изменены права сотрудника";
  if (action === "created") return "Новая заявка";
  return "Заявка обновлена";
}

function subjectTitle(item: AppNotification, snapshot: AppSnapshot | null): string {
  if (item.subject_kind === "issue_card") return snapshot?.issues.find((issue) => issue.id === item.subject_id)?.title ?? "Проблема дома";
  if (item.subject_kind === "resident_request") {
    const request = snapshot?.residentRequests.find((entry) => entry.id === item.subject_id);
    return request?.address ? `Доступ: ${request.address}` : "Заявка на доступ к дому";
  }
  if (item.subject_kind === "company_registration_request") return snapshot?.companyRequests.find((entry) => entry.id === item.subject_id)?.companyName ?? "Регистрация УК";
  if (item.subject_kind === "house_addition_request") return snapshot?.houseRequests.find((entry) => entry.id === item.subject_id)?.address ?? "Подключение дома";
  if (item.subject_kind === "resident_offer") {
    const offer = snapshot?.residentOffers.find((entry) => entry.id === item.subject_id);
    return snapshot?.houses.find((house) => house.id === offer?.houseId)?.address ?? "Предложение доступа к дому";
  }
  if (item.subject_kind === "resident_grant") {
    const grant = snapshot?.residentGrants.find((entry) => entry.id === item.subject_id);
    return snapshot?.houses.find((house) => house.id === grant?.houseId)?.address ?? "Доступ к дому";
  }
  return item.subject_kind === "staff_assignment" ? "Доступ сотрудника УК" : "Обновление сервиса";
}

function actionLabel(destination: NotificationDestination): string {
  if (destination === "issue") return "Открыть проблему";
  if (destination === "request" || destination === "support-request") return "Открыть заявку";
  if (destination === "admin-record") return "Открыть запись";
  if (destination === "home") return "Мои дома";
  if (destination === "access" || destination === "employee-access") return "Открыть доступы";
  return "";
}

interface Props {
  role: NotificationAudience;
  snapshot: AppSnapshot | null;
  items: AppNotification[];
  loading: boolean;
  error: string;
  busyId: string | null;
  onBack: () => void;
  onRefresh: () => void;
  onMarkAll: () => void;
  onOpen: (item: AppNotification) => void;
  onMarkRead: (item: AppNotification) => void;
}

export function NotificationCenter({ role, snapshot, items, loading, error, busyId, onBack, onRefresh, onMarkAll, onOpen, onMarkRead }: Props) {
  const unreadCount = items.filter((item) => !item.read_at).length;
  return <div className="page page--notifications notifications-page">
    <ScreenHeader title="Уведомления" subtitle={unreadCount ? `Непрочитанных на странице: ${unreadCount}` : "Обновления по вашим делам"} onBack={onBack} />
    <div className="notifications-page__toolbar"><p>Здесь те же события по заявкам и проблемам, о которых пишет бот.</p><div className="notifications-page__toolbar-actions"><button type="button" className="text-link" disabled={loading || busyId !== null || items.length === 0} onClick={onMarkAll}>{busyId === "all" ? "Отмечаем…" : "Прочитать все"}</button><button type="button" className="text-link" disabled={loading || busyId !== null} onClick={onRefresh}>Обновить</button></div></div>
    {loading && !items.length && <p className="muted-text" role="status">Загружаем уведомления…</p>}
    {error && <div className="panel notifications-page__error" role="alert"><p>{error}</p><button type="button" className="button button--soft" onClick={onRefresh}>Повторить</button></div>}
    {!loading && !error && !items.length && <div className="panel empty-state notifications-page__empty"><Icon name="bell" size={28} /><strong>Уведомлений пока нет</strong><p>Ответы и обновления появятся здесь.</p></div>}
    <div className="notifications-page__list">
      {items.map((item) => {
        const destination = notificationDestination(item, role);
        const unread = !item.read_at;
        return <article className={`notifications-page__item${unread ? " notifications-page__item--unread" : ""}`} key={item.id}>
          <div className="notifications-page__item-icon"><Icon name={item.subject_kind === "issue_card" ? "home" : "key"} size={20} /></div>
          <div className="notifications-page__item-content">
            <div className="notifications-page__item-heading"><strong>{eventTitle(item)}</strong>{unread && <span className="notifications-page__unread-dot" aria-label="Не прочитано" />}</div>
            <p>{subjectTitle(item, snapshot)}</p>
            <time dateTime={item.created_at}>{formatDate(item.created_at)}</time>
            {destination ? <button type="button" className="notifications-page__open" disabled={busyId !== null} onClick={() => onOpen(item)}>{busyId === item.id ? "Открываем…" : actionLabel(destination)} <Icon name="chevron" size={16} /></button> : <div className="notifications-page__unavailable"><span>Для этого события отдельной страницы пока нет.</span>{unread && <button type="button" className="text-link" disabled={busyId !== null} onClick={() => onMarkRead(item)}>Отметить прочитанным</button>}</div>}
          </div>
        </article>;
      })}
    </div>
  </div>;
}
