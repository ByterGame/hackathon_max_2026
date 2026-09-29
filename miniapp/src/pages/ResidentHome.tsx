import { useState } from "react";

import { IssueCard } from "../features/issues/ui/IssueCard";
import { type House, type Issue, type ResidentGrant, type ResidentRequest } from "../features/issues/types";
import { Icon } from "../shared/common_ui/Icon";
import { ScreenHeader } from "../shared/common_ui/ScreenHeader";

interface ResidentHomeProps {
  houses: House[];
  issues: Issue[];
  grants: ResidentGrant[];
  requests: ResidentRequest[];
  currentUserId: string;
  selectedHouseId: string;
  onSelectHouse: (id: string) => void;
  onNew: () => void;
  onIssue: (id: string) => void;
  onAccess: () => void;
  onChangeRole?: () => void;
}

export function ResidentHome({ houses, issues, grants, requests, currentUserId, selectedHouseId, onSelectHouse, onNew, onIssue, onAccess, onChangeRole }: ResidentHomeProps) {
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
      <ScreenHeader title="Мой дом" subtitle="Обращения и ответы УК" icon="home" action={onChangeRole ? { label: "Сменить демо-роль", onClick: onChangeRole, icon: "user" } : undefined} />
      <div className="house-card panel">
        <span className="small-icon"><Icon name="building" size={24} /></span>
        <span>{myHouseIds.length > 1 ? <select className="house-picker" aria-label="Выберите дом" value={house?.id} onChange={(event) => onSelectHouse(event.target.value)}>{myHouseIds.map((id) => <option key={id} value={id}>{houses.find((houseItem) => houseItem.id === id)?.address}</option>)}</select> : <strong>{house?.address ?? "Дом не выбран"}</strong>}<small><Icon name="check" size={15} /> {myGrants.filter((item) => item.houseId === house?.id).length > 1 ? `Доступ: ${myGrants.filter((item) => item.houseId === house?.id).length} квартиры` : `Доступ: подъезд №${grant?.entrance}, кв. ${grant?.apartment}`}</small></span>
      </div>
      <button type="button" className="text-link" onClick={onAccess}>Заявка на доступ к другому дому <Icon name="chevron" size={17} /></button>

      <button type="button" className="button button--primary button--wide create-button" onClick={onNew} disabled={!house}><Icon name="plus" size={25} /> Сообщить о проблеме</button>

      {requests.length > 0 && <button type="button" className="request-hint" onClick={onAccess}><Icon name="clock" size={18} /> Заявки на доступ: {requests.length} <Icon name="chevron" size={17} /></button>}

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
