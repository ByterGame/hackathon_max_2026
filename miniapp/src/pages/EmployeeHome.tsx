import { useMemo, useState } from "react";

import { IssueCard } from "../features/issues/ui/IssueCard";
import { formatDate, formatHouseCounts, formatIssueScope, issueStatusLabels, type House, type Issue } from "../features/issues/types";
import { formatCount } from "../shared/format_count";
import { Icon } from "../shared/common_ui/Icon";
import brandMark from "../assets/brand-mark.svg";

import "./employee-home.css";

type Filter = "all" | "open" | "reviewing" | "needs_info" | "in_progress" | "closed";
type FilterPanel = "status" | "category" | "house" | "search" | "sort" | null;

const statusFilters: { value: Filter; label: string }[] = [
  { value: "all", label: "Все" },
  { value: "open", label: "Открытые" },
  { value: "reviewing", label: "На рассмотрении" },
  { value: "needs_info", label: "Нужны уточнения" },
  { value: "in_progress", label: "В работе" },
  { value: "closed", label: "Закрытые" },
];

function updatedTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return formatDate(value);
  return new Intl.DateTimeFormat("ru-RU", { hour: "2-digit", minute: "2-digit" }).format(date);
}

function issueStatusLabel(issue: Issue): string {
  if (issue.status === "closed" && issue.closeResult === "solved") return "Решена";
  if (issue.status === "closed" && issue.closeResult === "invalid") return "Некорректная";
  return issueStatusLabels[issue.status];
}

export function EmployeeHome({ houses, issues, onIssue, onAccess, onNotifications, unreadCount, onChangeRole }: { houses: House[]; issues: Issue[]; onIssue: (id: string) => void; onAccess: () => void; onNotifications: () => void; unreadCount: number; onChangeRole?: () => void }) {
  const [filter, setFilter] = useState<Filter>("all");
  const [houseId, setHouseId] = useState("all");
  const [category, setCategory] = useState("all");
  const [query, setQuery] = useState("");
  const [sortOrder, setSortOrder] = useState<"newest" | "oldest">("newest");
  const [filterPanel, setFilterPanel] = useState<FilterPanel>(null);

  const counts = useMemo(() => {
    const today = new Date().toDateString();
    return {
      open: issues.filter((item) => item.status === "open").length,
      reviewing: issues.filter((item) => item.status === "reviewing").length,
      inProgress: issues.filter((item) => item.status === "in_progress").length,
      resolvedToday: issues.filter((item) => item.status === "closed" && item.closeResult === "solved" && new Date(item.updatedAt).toDateString() === today).length,
    };
  }, [issues]);

  const categories = useMemo(() => [...new Set(issues.map((item) => item.category).filter(Boolean))].sort((a, b) => a.localeCompare(b, "ru")), [issues]);
  const houseById = useMemo(() => new Map(houses.map((house) => [house.id, house])), [houses]);

  const visible = useMemo(() => issues.filter((item) => {
    const matchesStatus = filter === "all"
      || (filter === "open" && item.status === "open")
      || (filter === "reviewing" && item.status === "reviewing")
      || (filter === "needs_info" && item.status === "needs_info")
      || (filter === "in_progress" && item.status === "in_progress")
      || (filter === "closed" && item.status === "closed");
    return (houseId === "all" || item.houseId === houseId)
      && (category === "all" || item.category === category)
      && matchesStatus
      && `${item.title} ${item.description} ${item.summaryDescription}`.toLowerCase().includes(query.trim().toLowerCase());
  }).sort((a, b) => sortOrder === "newest"
    ? b.updatedAt.localeCompare(a.updatedAt)
    : a.updatedAt.localeCompare(b.updatedAt)), [issues, houseId, category, filter, query, sortOrder]);

  const houseGroups = useMemo(() => {
    const ids = [...new Set(visible.map((issue) => issue.houseId))];
    return ids.map((id) => ({ id, house: houseById.get(id), issues: visible.filter((issue) => issue.houseId === id) }));
  }, [houseById, visible]);

  const metrics = [
    { label: "Новые", count: counts.open, tone: "blue" },
    { label: "На рассмотрении", count: counts.reviewing, tone: "purple" },
    { label: "В работе", count: counts.inProgress, tone: "teal" },
    { label: "Решены сегодня", count: counts.resolvedToday, tone: "green" },
  ];

  const togglePanel = (next: Exclude<FilterPanel, null>) => setFilterPanel((current) => current === next ? null : next);
  const hasActiveFilters = filter !== "all" || houseId !== "all" || category !== "all" || query.trim() !== "" || sortOrder === "oldest";
  const clearFilters = () => {
    setFilter("all");
    setHouseId("all");
    setCategory("all");
    setQuery("");
    setSortOrder("newest");
    setFilterPanel(null);
  };
  const emptyTitle = issues.length ? "Ничего не найдено" : "Проблем пока нет";
  const emptyDescription = issues.length ? "Попробуйте изменить фильтры или запрос." : "Когда жильцы сообщат о проблеме, она появится здесь.";

  return (
    <div className="page page--employee employee-home">
      <aside className="employee-home__sidebar" aria-label="Навигация УК">
        <div className="employee-home__brand"><span className="employee-home__brand-mark"><img src={brandMark} alt="" width="38" height="38" /></span><span>СвойДом <small>УК</small></span></div>
        <button type="button" className="employee-home__nav-item employee-home__nav-item--active" onClick={() => window.scrollTo({ top: 0, behavior: "smooth" })}>Управление домами</button>
        <button type="button" className="employee-home__nav-item" onClick={() => document.getElementById("employee-issues")?.scrollIntoView({ behavior: "smooth" })}>Проблемы</button>
        <button type="button" className="employee-home__nav-item" onClick={onAccess}>Доступы</button>
        {onChangeRole && <button type="button" className="employee-home__nav-item employee-home__nav-item--role" onClick={onChangeRole}>Сменить демо-роль</button>}
      </aside>

      <main className="employee-home__main">
        <header className="employee-home__header">
          <h1>Управление домами</h1>
          <button type="button" className="icon-button employee-home__notifications" aria-label={unreadCount ? `Уведомления: ${unreadCount} непрочитанных` : "Уведомления"} onClick={onNotifications}><Icon name="bell" size={20} />{unreadCount > 0 && <span className="icon-button__badge" aria-hidden="true">{unreadCount > 99 ? "99+" : unreadCount}</span>}</button>
        </header>

        <div className="employee-home__metrics" aria-label="Статистика проблем">
          {metrics.map((metric) => <div className={`employee-home__metric employee-home__metric--${metric.tone}`} key={metric.label}><strong>{metric.count}</strong><span>{metric.label}</span></div>)}
        </div>

        <div className="employee-home__filters" id="employee-issues" aria-label="Фильтры проблем">
          <button type="button" className={`employee-home__chip ${!hasActiveFilters ? "employee-home__chip--selected" : ""}`} onClick={clearFilters}>Все</button>
          <button type="button" className={`employee-home__chip ${filter !== "all" ? "employee-home__chip--selected" : ""}`} onClick={() => togglePanel("status")} aria-expanded={filterPanel === "status"}>Статус{filter !== "all" && <span className="employee-home__chip-dot" />}</button>
          <button type="button" className={`employee-home__chip employee-home__chip--category ${category !== "all" ? "employee-home__chip--selected" : ""}`} onClick={() => togglePanel("category")} aria-expanded={filterPanel === "category"}>Категория{category !== "all" && <span className="employee-home__chip-dot" />}</button>
          <button type="button" className={`employee-home__chip ${houseId !== "all" ? "employee-home__chip--selected" : ""}`} onClick={() => togglePanel("house")} aria-expanded={filterPanel === "house"}>Дом{houseId !== "all" && <span className="employee-home__chip-dot" />}</button>
          <button type="button" className={`employee-home__chip ${sortOrder === "oldest" ? "employee-home__chip--selected" : ""}`} onClick={() => togglePanel("sort")} aria-expanded={filterPanel === "sort"}>Сортировка: {sortOrder === "newest" ? "новые" : "старые"}</button>
          <button type="button" className={`employee-home__chip employee-home__chip--search ${query ? "employee-home__chip--selected" : ""}`} onClick={() => togglePanel("search")} aria-label="Найти проблему" aria-expanded={filterPanel === "search"}><Icon name="search" size={17} /><span>Поиск</span></button>
        </div>

        {filterPanel && <div className="employee-home__filter-panel" aria-label="Настройка фильтра">
          {filterPanel === "status" && <div className="employee-home__filter-options" role="group" aria-label="Состояние проблемы">
            {statusFilters.map((option) => <button type="button" key={option.value} className={filter === option.value ? "is-selected" : ""} onClick={() => { setFilter(option.value); setFilterPanel(null); }}>{option.label}</button>)}
          </div>}
          {filterPanel === "category" && <label className="employee-home__filter-field"><span>Категория</span><select value={category} onChange={(event) => { setCategory(event.target.value); setFilterPanel(null); }}><option value="all">Все категории</option>{categories.map((item) => <option key={item} value={item}>{item}</option>)}</select></label>}
          {filterPanel === "house" && <label className="employee-home__filter-field"><span>Дом</span><select value={houseId} onChange={(event) => { setHouseId(event.target.value); setFilterPanel(null); }}><option value="all">Все дома</option>{houses.map((house) => <option key={house.id} value={house.id}>{house.address}</option>)}</select></label>}
          {filterPanel === "sort" && <div className="employee-home__filter-options" role="group" aria-label="Порядок проблем по времени обновления"><button type="button" className={sortOrder === "newest" ? "is-selected" : ""} onClick={() => { setSortOrder("newest"); setFilterPanel(null); }}>Сначала новые</button><button type="button" className={sortOrder === "oldest" ? "is-selected" : ""} onClick={() => { setSortOrder("oldest"); setFilterPanel(null); }}>Сначала старые</button></div>}
          {filterPanel === "search" && <label className="employee-home__filter-field"><span>Поиск по названию и описанию</span><input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Найти проблему" /></label>}
        </div>}

        <span className="employee-home__sr-only" role="status">{formatCount(visible.length, ["Найдена проблема", "Найдены проблемы", "Найдено проблем"])}</span>

        <div className="employee-home__desktop-table" aria-label="Проблемы домов">
          <div className="employee-home__table-heading"><span>Проблема</span><span>Дом / область</span><span>Статус</span><span>Поддержали</span><span>Обновлено</span></div>
          {visible.map((issue) => <button type="button" className="employee-home__table-row" key={issue.id} onClick={() => onIssue(issue.id)} aria-label={`Открыть проблему «${issue.title}». ${houseById.get(issue.houseId)?.address ?? "Дом не указан"}, ${formatIssueScope(issue.scope)}. Статус: ${issueStatusLabel(issue)}. Поддержали: ${issue.supportsCount}. Обновлено: ${formatDate(issue.updatedAt)}.`}>
            <strong>{issue.title}</strong>
            <span>{houseById.get(issue.houseId)?.address ?? "Дом не указан"} · {formatIssueScope(issue.scope)}</span>
            <span className="employee-home__status-slot"><span className={`employee-home__row-status employee-home__row-status--${issue.status}${issue.status === "closed" && issue.closeResult === "invalid" ? " employee-home__row-status--invalid" : ""}`}>{issueStatusLabel(issue)}</span></span>
            <span className="employee-home__support-count">{issue.supportsCount}</span>
            <time dateTime={issue.updatedAt}>{updatedTime(issue.updatedAt)}</time>
          </button>)}
          {!visible.length && <div className="employee-home__table-empty"><strong>{emptyTitle}</strong><p>{emptyDescription}</p>{hasActiveFilters && <button type="button" className="employee-home__empty-reset" onClick={clearFilters}>Сбросить фильтры</button>}</div>}
        </div>

        <div className="employee-home__mobile-feed" id="employee-mobile-issues">
          {houseGroups.map(({ id, house, issues: houseIssues }) => {
            return <section className="employee-home__house-group" key={id}>
              <div className="employee-home__house-heading"><h2>{house?.address ?? "Дом не указан"}</h2><span className="employee-home__sr-only">{house ? `${formatHouseCounts(house.entranceCount, house.apartmentCount)} · ` : ""}{formatCount(houseIssues.length, ["проблема", "проблемы", "проблем"])}</span></div>
              <div className="employee-home__cards">{houseIssues.map((issue) => <IssueCard key={issue.id} issue={issue} onOpen={() => onIssue(issue.id)} />)}</div>
            </section>;
          })}
          {!visible.length && <div className="employee-home__empty"><Icon name="search" size={28} /><strong>{emptyTitle}</strong><p>{emptyDescription}</p>{hasActiveFilters && <button type="button" className="employee-home__empty-reset" onClick={clearFilters}>Сбросить фильтры</button>}</div>}
        </div>
      </main>
    </div>
  );
}
