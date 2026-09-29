import { Icon } from "../../../shared/common_ui/Icon";
import { formatDate, formatIssueScope, issueStatusLabels, type Issue } from "../types";

export function IssueCard({ issue, onOpen, compact = false }: { issue: Issue; onOpen: () => void; compact?: boolean }) {
  return (
    <button type="button" className={`issue-card ${compact ? "issue-card--compact" : ""}`} onClick={onOpen}>
      <span className="issue-card__top">
        <span className="issue-card__title">{issue.title}</span>
        <span className={`status status--${issue.status}`}>{issue.status === "closed" && issue.closeResult === "solved" ? "Решена" : issueStatusLabels[issue.status]}</span>
      </span>
      <span className="issue-card__meta"><Icon name="pin" size={16} /> {formatIssueScope(issue.scope)}</span>
      {!compact && <span className="issue-card__description">{issue.summaryDescription || issue.description}</span>}
      <span className="issue-card__bottom">
        <span><Icon name="people" size={18} /> Поддержали: <strong>{issue.supportsCount}</strong></span>
        <span>{formatDate(issue.updatedAt)} <Icon name="chevron" size={19} /></span>
      </span>
    </button>
  );
}
