import { useMemo, useState } from "react";

import { IssueCard } from "../features/issues/ui/IssueCard";
import type { House, Issue, IssueStatus } from "../features/issues/types";
import { Icon } from "../shared/common_ui/Icon";
import { ScreenHeader } from "../shared/common_ui/ScreenHeader";

type Filter = "all" | "open" | "working" | "closed";

export function EmployeeHome({ houses, issues, onIssue, onAccess, onChangeRole }: { houses: House[]; issues: Issue[]; onIssue: (id: string) => void; onAccess: () => void; onChangeRole?: () => void }) {
  const [filter, setFilter] = useState<Filter>("all");
  const [houseId, setHouseId] = useState("all");
  const [query, setQuery] = useState("");

  const counts = useMemo(() => ({
    open: issues.filter((item) => item.status === "open").length,
    working: issues.filter((item) => ["reviewing", "in_progress"].includes(item.status)).length,
    info: issues.filter((item) => item.status === "needs_info").length,
  }), [issues]);

  const matchesFilter = (status: IssueStatus) => filter === "all"
    || (filter === "open" && status === "open")
    || (filter === "working" && ["reviewing", "needs_info", "in_progress"].includes(status))
    || (filter === "closed" && status === "closed");

  const visible = issues.filter((item) =>
    (houseId === "all" || item.houseId === houseId)
    && matchesFilter(item.status)
    && `${item.title} ${item.description}`.toLowerCase().includes(query.toLowerCase()),
  );

  return (
    <div className="page page--employee">
      <ScreenHeader title="Управление домами" subtitle="Панель УК" icon="building" action={onChangeRole ? { label: "Сменить демо-роль", onClick: onChangeRole, icon: "user" } : undefined} />
      <div className="stats-grid">
        <div className="stat-card stat-card--blue"><span className="small-icon"><Icon name="list" /></span><span>Открыты</span><strong>{counts.open}</strong></div>
        <div className="stat-card stat-card--amber"><span className="small-icon"><Icon name="clock" /></span><span>В работе</span><strong>{counts.working}</strong></div>
        <div className="stat-card stat-card--red"><span className="small-icon"><Icon name="chat" /></span><span>Нужны уточнения</span><strong>{counts.info}</strong></div>
      </div>
      <button type="button" className="button button--soft button--wide employee-access-button" onClick={onAccess}><Icon name="key" size={20} /> Жильцы, сотрудники и дома <Icon name="chevron" size={18} /></button>

      <section className="staff-controls panel">
        <div className="section-heading"><h2>Карточки проблем</h2><span className="count-badge">{visible.length}</span></div>
        <div className="staff-controls__row">
          <label className="field field--inline"><span>Дом</span><select value={houseId} onChange={(event) => setHouseId(event.target.value)}><option value="all">Все дома</option>{houses.map((house) => <option key={house.id} value={house.id}>{house.address}</option>)}</select></label>
          <label className="search-field"><Icon name="search" size={19} /><input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Найти проблему" aria-label="Найти проблему" /></label>
        </div>
      </section>

      <div className="segmented segmented--four" role="tablist" aria-label="Фильтр проблем">
        {([ ["all", "Все"], ["open", "Открытые"], ["working", "В работе"], ["closed", "Закрытые"] ] as const).map(([value, label]) => (
          <button key={value} type="button" role="tab" aria-selected={filter === value} className={filter === value ? "is-active" : ""} onClick={() => setFilter(value)}>{label}</button>
        ))}
      </div>

      <div className="house-groups" id="employee-issues">
        {houses.filter((house) => houseId === "all" || house.id === houseId).map((house) => {
          const houseIssues = visible.filter((item) => item.houseId === house.id).sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
          if (!houseIssues.length) return null;
          return <section className="house-group" key={house.id}><div className="section-heading"><div><h2>{house.address}</h2><p>{houseIssues.length} проблем</p></div><Icon name="building" size={21} /></div><div className="employee-grid">{houseIssues.map((issue) => <IssueCard key={issue.id} issue={issue} onOpen={() => onIssue(issue.id)} />)}</div></section>;
        })}
        {!visible.length && <div className="empty-state panel"><Icon name="search" size={30} /><strong>Ничего не найдено</strong><p>Попробуйте другой дом, состояние или запрос.</p></div>}
      </div>
    </div>
  );
}
