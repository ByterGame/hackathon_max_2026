import { Icon } from "../shared/common_ui/Icon";
import { ScreenHeader } from "../shared/common_ui/ScreenHeader";

export function PhoneGate({ busy, error, onConfirm }: { busy: boolean; error: string; onConfirm: () => void }) {
  return <div className="page page--onboarding"><ScreenHeader title="Подтвердить номер" subtitle="Вход через MAX" icon="home" /><section className="panel onboarding-hero"><span className="small-icon"><Icon name="shield" size={24} /></span><h2>Нужен ваш номер в MAX</h2><p>Он свяжет ваш аккаунт с заявкой на дом или персональным доступом сотрудника УК. Подтверждение номера само по себе не даёт доступ к квартире.</p>{error && <p className="form-error" role="alert">{error}</p>}<button type="button" className="button button--primary button--wide" disabled={busy} onClick={onConfirm}>{busy ? "Проверяем…" : "Подтвердить номер в MAX"}</button></section></div>;
}
