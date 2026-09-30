import { useState } from "react";

import brandMark from "../assets/brand-mark.svg";
import { IssueCard } from "../features/issues/ui/IssueCard";
import { formatApartmentLocation, type CompanyRegistrationRequest, type House, type HouseAdditionRequest, type Issue, type ResidentGrant, type ResidentRequest } from "../features/issues/types";
import { Icon } from "../shared/common_ui/Icon";
import { ScreenHeader } from "../shared/common_ui/ScreenHeader";
import { ApplicantRequestsList, type ApplicantCase } from "./MyRequests";
import "./resident-home.css";

interface ResidentHomeProps {
  view: "home" | "issues";
  houses: House[];
  issues: Issue[];
  grants: ResidentGrant[];
  requests: ResidentRequest[];
  companyRequests: CompanyRegistrationRequest[];
  houseRequests: HouseAdditionRequest[];
  currentUserId: string;
  selectedHouseId: string;
  onSelectHouse: (id: string) => void;
  onHome: () => void;
  onIssues: () => void;
  onNew: () => void;
  onIssue: (id: string) => void;
  onAccess: () => void;
  onRegister: () => void;
  onRequest: (request: ApplicantCase) => void;
  onAllRequests: () => void;
  onNotifications: () => void;
  unreadCount: number;
  onChangeRole?: () => void;
}

export function ResidentHome({ view, houses, issues, grants, requests, companyRequests, houseRequests, currentUserId, selectedHouseId, onSelectHouse, onHome, onIssues, onNew, onIssue, onAccess, onRegister, onRequest, onAllRequests, onNotifications, unreadCount, onChangeRole }: ResidentHomeProps) {
  const [closed, setClosed] = useState(false);
  const [mineOnly, setMineOnly] = useState(false);
  const [filterOpen, setFilterOpen] = useState(false);
  const [category, setCategory] = useState("");
  const [sortOrder, setSortOrder] = useState<"newest" | "oldest">("newest");
  const myGrants = grants.filter((item) => item.status !== "revoked" && item.status !== "expired" && (!item.validUntil || new Date(item.validUntil).getTime() > Date.now()));
  const myHouseIds = [...new Set(myGrants.map((item) => item.houseId))];
  const grant = myGrants.find((item) => item.houseId === selectedHouseId) ?? myGrants[0];
  const house = houses.find((item) => item.id === grant?.houseId);
  const houseIssues = issues.filter((item) => item.houseId === house?.id);
  const recentIssues = houseIssues
    .filter((item) => item.status !== "closed")
    .sort((a, b) => b.updatedAt.localeCompare(a.updatedAt))
    .slice(0, 2);
  const categories = [...new Set(houseIssues.map((item) => item.category).filter(Boolean))].sort((a, b) => a.localeCompare(b, "ru"));
  const selectedCategory = categories.includes(category) ? category : "";
  const shownIssues = houseIssues
    .filter((item) => (closed ? item.status === "closed" : item.status !== "closed"))
    .filter((item) => !mineOnly || item.supportedByMe || item.authorId === currentUserId)
    .filter((item) => !selectedCategory || item.category === selectedCategory)
    .sort((a, b) => sortOrder === "newest"
      ? b.updatedAt.localeCompare(a.updatedAt)
      : a.updatedAt.localeCompare(b.updatedAt));

  return (
    <div className={`page resident-home resident-home--${view}`}>
      <header className="resident-home__web-header">
        <div className="resident-home__web-header-inner">
          <button type="button" className="resident-home__web-brand" onClick={onHome} aria-label="На главную">
            <img src={brandMark} alt="" width="52" height="52" /><strong>СвойДом</strong>
          </button>
          <div className="resident-home__web-actions">
            <button type="button" className="resident-home__web-address" onClick={onHome} title="Выбрать дом">{house?.address ?? "Мои дома"} · Житель</button>
            <button type="button" className="icon-button resident-home__web-bell" aria-label={unreadCount ? `Уведомления: ${unreadCount} непрочитанных` : "Уведомления"} onClick={onNotifications}><Icon name="bell" size={20} />{unreadCount > 0 && <span className="icon-button__badge" aria-hidden="true">{unreadCount > 99 ? "99+" : unreadCount}</span>}</button>
            {onChangeRole && <button type="button" className="resident-home__web-role" onClick={onChangeRole}>Сменить роль</button>}
          </div>
        </div>
      </header>
      <div className="resident-home__content">
        {view === "home" ? <>
          <ScreenHeader brand title="СвойДом" subtitle="Проблемы дома и ответы УК" icon="home" actions={[{ label: "Уведомления", onClick: onNotifications, icon: "bell", badge: unreadCount }, ...(onChangeRole ? [{ label: "Сменить демо-роль", onClick: onChangeRole, icon: "user" as const }] : [])]} />
          <section className="my-houses" aria-labelledby="my-houses-title">
            <h2 id="my-houses-title">Мои дома</h2>
            <div className="my-houses__list">{myHouseIds.map((id) => {
              const item = houses.find((candidate) => candidate.id === id);
              const locations = myGrants
                .filter((entry) => entry.houseId === id)
                .map((entry) => formatApartmentLocation(entry.apartment, entry.entrance).replace(", ", " · "));
              const selected = house?.id === id;
              const content = <>
                <strong className="my-house-card__address">{item?.address ?? "Дом без адреса"}</strong>
                <span className="my-house-card__location">{locations.join("; ")}</span>
                {myHouseIds.length > 1 && <span className="my-house-card__state" aria-hidden="true"><Icon name={selected ? "check" : "chevron"} size={20} /></span>}
              </>;
              const className = `panel my-house-card${selected ? " my-house-card--selected" : ""}`;
              return myHouseIds.length > 1
                ? <button key={id} type="button" className={className} aria-label={`${item?.address ?? "Дом без адреса"}: ${selected ? "текущий дом" : "переключиться на этот дом"}`} aria-pressed={selected} onClick={() => onSelectHouse(id)}>{content}</button>
                : <div key={id} className={className}>{content}</div>;
            })}</div>
          </section>
          <section className="resident-home__recent" aria-labelledby="recent-issues-title">
            <h2 id="recent-issues-title">Актуально в доме</h2>
            <div className="issue-list">
              {recentIssues.length ? recentIssues.map((issue) => <IssueCard key={issue.id} issue={issue} onOpen={() => onIssue(issue.id)} />) : <div className="empty-state panel"><Icon name="check" size={30} /><strong>Актуальных проблем нет</strong><p>Новые сообщения жителей появятся здесь.</p></div>}
            </div>
          </section>
          <button type="button" className="button button--primary button--wide resident-home__create" onClick={onNew} disabled={!house}>+ Сообщить о проблеме</button>
          <button type="button" className="text-link resident-home__all-issues" onClick={onIssues}>Все проблемы дома <Icon name="chevron" size={17} /></button>
          <section className="resident-home__access" aria-label="Доступ и заявки">
            <ApplicantRequestsList residentRequests={requests} companyRequests={companyRequests} houseRequests={houseRequests} onOpen={onRequest} limit={3} />
            {requests.length + companyRequests.length + houseRequests.length > 3 && <button type="button" className="text-link" onClick={onAllRequests}>Все заявки <Icon name="chevron" size={17} /></button>}
            <button type="button" className="text-link" onClick={onAccess}>Заявка на доступ к другому дому <Icon name="chevron" size={17} /></button>
            <button type="button" className="text-link" onClick={onRegister}>Представляете УК? Подключить компанию <Icon name="chevron" size={17} /></button>
          </section>
        </> : <section className="issues-section" id="issues-list" aria-label={`Проблемы дома: ${houseIssues.length}`}>
          <div className="section-heading"><h1>Проблемы дома</h1></div>
          <div className="segmented" role="tablist" aria-label="Состояние проблем">
            <button type="button" role="tab" aria-selected={!closed} className={!closed ? "is-active" : ""} onClick={() => setClosed(false)}>Активные</button>
            <button type="button" role="tab" aria-selected={closed} className={closed ? "is-active" : ""} onClick={() => setClosed(true)}>Закрытые</button>
          </div>
          <div className="filter-chips" aria-label="Фильтры проблем">
            <button type="button" className={!mineOnly ? "chip chip--active" : "chip"} aria-pressed={!mineOnly} onClick={() => setMineOnly(false)}>Все</button>
            <button type="button" className={mineOnly ? "chip chip--active" : "chip"} aria-pressed={mineOnly} onClick={() => setMineOnly(true)}>Я участвую</button>
            <button type="button" className={filterOpen || selectedCategory || closed || sortOrder === "oldest" ? "chip chip--active" : "chip"} aria-expanded={filterOpen} aria-controls="resident-issue-filters" onClick={() => setFilterOpen((value) => !value)}>Фильтры</button>
          </div>
          {filterOpen && <div className="resident-home__filter-panel" id="resident-issue-filters">
            <label>Состояние<select value={closed ? "closed" : "active"} onChange={(event) => setClosed(event.target.value === "closed")}><option value="active">Активные</option><option value="closed">Закрытые</option></select></label>
            <label>Категория<select value={selectedCategory} onChange={(event) => setCategory(event.target.value)}><option value="">Все категории</option>{categories.map((item) => <option key={item} value={item}>{item}</option>)}</select></label>
            <label>По времени обновления<select value={sortOrder} onChange={(event) => setSortOrder(event.target.value as "newest" | "oldest")}><option value="newest">Сначала новые</option><option value="oldest">Сначала старые</option></select></label>
          </div>}
          <div className="issue-list">
            {shownIssues.length ? shownIssues.map((issue) => <IssueCard key={issue.id} issue={issue} onOpen={() => onIssue(issue.id)} />) : <div className="empty-state panel"><Icon name="check" size={30} /><strong>Пока нет проблем</strong><p>{mineOnly ? "Вы ещё не участвовали в таких карточках." : "В этой группе проблем нет."}</p></div>}
          </div>
          <button type="button" className="button button--primary resident-home__web-create" onClick={onNew} disabled={!house}>Сообщить о проблеме</button>
        </section>}
      </div>
    </div>
  );
}
