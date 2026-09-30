import { Icon, type IconName } from "./Icon";
import brandMark from "../../assets/brand-mark.svg";

interface ScreenHeaderProps {
  title: string;
  subtitle?: string;
  icon?: IconName;
  brand?: boolean;
  onBack?: () => void;
  action?: { label: string; onClick: () => void; icon: "bell" | "user" | "close"; badge?: number };
  actions?: { label: string; onClick: () => void; icon: "bell" | "user" | "close"; badge?: number }[];
}

export function ScreenHeader({ title, subtitle, icon, brand = false, onBack, action, actions }: ScreenHeaderProps) {
  const trailingActions = actions ?? (action ? [action] : []);
  return (
    <header className={`screen-header${brand ? " screen-header--brand" : ""}`}>
      {onBack ? (
        <button type="button" className="icon-button icon-button--soft" aria-label="Назад" onClick={onBack}><Icon name="back" /></button>
      ) : (
        <span className="brand-mark">
          {!icon || icon === "home" || icon === "building"
            ? <img src={brandMark} alt="" width="44" height="44" />
            : <Icon name={icon} size={27} />}
        </span>
      )}
      <div className="screen-header__text"><h1>{title}</h1>{subtitle && <p>{subtitle}</p>}</div>
      {trailingActions.length > 0 && <span className="screen-header__actions">{trailingActions.map((item) => <button key={item.label} type="button" className="icon-button icon-button--header-action" aria-label={item.badge ? `${item.label}: ${item.badge} непрочитанных` : item.label} title={item.label} onClick={item.onClick}><Icon name={item.icon} />{Boolean(item.badge) && <span className="icon-button__badge" aria-hidden="true">{item.badge! > 99 ? "99+" : item.badge}</span>}</button>)}</span>}
    </header>
  );
}
