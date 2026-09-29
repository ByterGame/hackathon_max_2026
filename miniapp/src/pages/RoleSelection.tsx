import { useState } from "react";

import { Icon } from "../shared/common_ui/Icon";
import { ScreenHeader } from "../shared/common_ui/ScreenHeader";
import type { Role } from "../features/issues/types";

export function RoleSelection({ onContinue, onRegister }: { onContinue: (role: Role) => void; onRegister: () => void }) {
  const [role, setRole] = useState<Role>("resident");
  const [showCompanyInfo, setShowCompanyInfo] = useState(false);

  return (
    <div className="page page--welcome">
      <ScreenHeader title="Управление домами" subtitle="Сервис обращений" icon="building" />
      <section className="welcome-intro">
        <span className="eyebrow">Добро пожаловать</span>
        <h2>Дом начинается с диалога</h2>
        <p>Сообщайте о проблемах, объединяйтесь с соседями и следите за ответами управляющей компании.</p>
      </section>

      <section className="section-stack" aria-label="Выбор роли для демонстрации">
        <div className="section-heading"><h3>Посмотреть как</h3><span className="demo-pill">Демо</span></div>
        <button type="button" className={`role-card ${role === "resident" ? "role-card--selected" : ""}`} onClick={() => setRole("resident")} aria-pressed={role === "resident"}>
          <span className="role-card__icon"><Icon name="home" size={29} /></span>
          <span className="role-card__body"><strong>Житель</strong><small>Проблемы дома и ответы УК</small></span>
          <span className="radio-dot" />
        </button>
        <button type="button" className={`role-card ${role === "employee" ? "role-card--selected" : ""}`} onClick={() => setRole("employee")} aria-pressed={role === "employee"}>
          <span className="role-card__icon role-card__icon--slate"><Icon name="building" size={29} /></span>
          <span className="role-card__body"><strong>Сотрудник УК</strong><small>Карточки домов и обновление статусов</small></span>
          <span className="radio-dot" />
        </button>
      </section>

      <section className="welcome-access panel">
        <span className="small-icon small-icon--green"><Icon name="shield" size={20} /></span>
        <div><strong>Доступ к дому</strong><p>В рабочем сервисе доступ жителю выдаёт УК после рассмотрения заявки. Доступ сотруднику выдаёт поддержка или уполномоченный коллега.</p></div>
      </section>

      <div className="welcome-actions">
        <button type="button" className="button button--primary button--wide" onClick={() => onContinue(role)}>Открыть демо <Icon name="arrow" /></button>
        <button type="button" className="button button--soft button--wide" onClick={onRegister}>Подключить управляющую компанию</button>
        <button type="button" className="button button--subtle button--wide" onClick={() => setShowCompanyInfo(!showCompanyInfo)} aria-expanded={showCompanyInfo}>Как подключить УК? <Icon name="chevron" size={18} /></button>
        {showCompanyInfo && <div className="info-panel">Представитель УК подаёт обращение на подключение. Наша поддержка проверяет компанию и дома, затем выдаёт первый персональный доступ. Общих паролей для сотрудников нет.</div>}
      </div>
      <div className="city-illustration" aria-hidden="true"><span /><span /><span /><span /><span /></div>
    </div>
  );
}
