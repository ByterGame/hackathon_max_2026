import { useState } from "react";

import { IssueCard } from "../features/issues/ui/IssueCard";
import { formatApartmentLocation, formatHouseCounts, type CompanyRegistrationRequest, type House, type HouseAdditionRequest, type Issue, type ResidentGrant, type ResidentRequest } from "../features/issues/types";
import { Icon } from "../shared/common_ui/Icon";
import { ScreenHeader } from "../shared/common_ui/ScreenHeader";
import { ApplicantRequestsList, type ApplicantCase } from "./MyRequests";
import "./resident-home.css";

interface ResidentHomeProps {
  houses: House[];
  issues: Issue[];
  grants: ResidentGrant[];
  requests: ResidentRequest[];
  companyRequests: CompanyRegistrationRequest[];
  houseRequests: HouseAdditionRequest[];
  currentUserId: string;
  selectedHouseId: string;
  onSelectHouse: (id: string) => void;
  onNew: () => void;
  onIssue: (id: string) => void;
  onAccess: () => void;
  onRegister: () => void;
  onRequest: (request: ApplicantCase) => void;
  onAllRequests: () => void;
  onChangeRole?: () => void;
}

export function ResidentHome({ houses, issues, grants, requests, companyRequests, houseRequests, currentUserId, selectedHouseId, onSelectHouse, onNew, onIssue, onAccess, onRegister, onRequest, onAllRequests, onChangeRole }: ResidentHomeProps) {
  const [closed, setClosed] = useState(false);
  const [mineOnly, setMineOnly] = useState(false);
  const myGrants = grants.filter((item) => item.status !== "revoked" && item.status !== "expired" && (!item.validUntil || new Date(item.validUntil).getTime() > Date.now()));
  const myHouseIds = [...new Set(myGrants.map((item) => item.houseId))];
  const grant = myGrants.find((item) => item.houseId === selectedHouseId) ?? myGrants[0];
  const house = houses.find((item) => item.id === grant?.houseId);
  const shownIssues = issues
    .filter((item) => item.houseId === house?.id)
    .filter((item) => (closed ? item.status === "closed" : item.status !== "closed"))
    .filter((item) => !mineOnly || item.supportedByMe || item.authorId === currentUserId)
    .sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));

  return (
    <div className="page">
      <ScreenHeader title="Мои дома" subtitle="Обращения и ответы УК" icon="home" action={onChangeRole ? { label: "Сменить демо-роль", onClick: onChangeRole, icon: "user" } : undefined} />
      <section className="my-houses" aria-labelledby="my-houses-title">
        <div className="section-heading"><h2 id="my-houses-title">Мои дома</h2><span className="count-badge">{myHouseIds.length}</span></div>
        <div className="my-houses__list">{myHouseIds.map((id) => {
          const item = houses.find((candidate) => candidate.id === id);
          const locations = myGrants.filter((entry) => entry.houseId === id).map((entry) => formatApartmentLocation(entry.apartment, entry.entrance));
          const selected = house?.id === id;
          return <button key={id} type="button" className={`panel my-house-card${selected ? " my-house-card--selected" : ""}`} aria-pressed={selected} onClick={() => onSelectHouse(id)}>
            <span className="small-icon"><Icon name="building" size={24} /></span>
            <span className="my-house-card__text"><strong>{item?.address ?? "Дом без адреса"}</strong><small>Доступ: {locations.join(", ")}</small>{item && <small>{formatHouseCounts(item.entranceCount, item.apartmentCount)}</small>}</span>
            <span className="my-house-card__state">{selected ? "Выбран" : "Выбрать"}</span>
          </button>;
        })}</div>
      </section>
      <button type="button" className="text-link" onClick={onAccess}>Заявка на доступ к другому дому <Icon name="chevron" size={17} /></button>
      <button type="button" className="text-link" onClick={onRegister}>Представляете УК? Подключить компанию <Icon name="chevron" size={17} /></button>

      <button type="button" className="button button--primary button--wide create-button" onClick={onNew} disabled={!house}><Icon name="plus" size={25} /> Сообщить о проблеме</button>

      <ApplicantRequestsList residentRequests={requests} companyRequests={companyRequests} houseRequests={houseRequests} onOpen={onRequest} limit={3} />
      {requests.length + companyRequests.length + houseRequests.length > 3 && <button type="button" className="text-link" onClick={onAllRequests}>Все заявки <Icon name="chevron" size={17} /></button>}

      <section className="issues-section" id="issues-list">
        <div className="section-heading"><h2>Проблемы дома</h2><span className="count-badge">{issues.filter((item) => item.houseId === house?.id).length}</span></div>
        <div className="segmented" role="tablist" aria-label="Состояние проблем">
          <button type="button" role="tab" aria-selected={!closed} className={!closed ? "is-active" : ""} onClick={() => setClosed(false)}>Активные</button>
          <button type="button" role="tab" aria-selected={closed} className={closed ? "is-active" : ""} onClick={() => setClosed(true)}>Закрытые</button>
        </div>
        <div className="filter-chips" aria-label="Участие в проблеме">
          <button type="button" className={!mineOnly ? "chip chip--active" : "chip"} onClick={() => setMineOnly(false)}><Icon name="list" size={17} /> Все</button>
          <button type="button" className={mineOnly ? "chip chip--active" : "chip"} onClick={() => setMineOnly(true)}><Icon name="people" size={18} /> Я участвую</button>
        </div>
        <div className="issue-list">
          {shownIssues.length ? shownIssues.map((issue) => <IssueCard key={issue.id} issue={issue} onOpen={() => onIssue(issue.id)} />) : <div className="empty-state panel"><Icon name="check" size={30} /><strong>Пока нет проблем</strong><p>{mineOnly ? "Вы ещё не участвовали в таких карточках." : "В этой группе проблем нет."}</p></div>}
        </div>
      </section>
    </div>
  );
}
