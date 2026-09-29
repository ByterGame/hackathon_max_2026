import { Icon, type IconName } from "./Icon";

interface ScreenHeaderProps {
  title: string;
  subtitle?: string;
  icon?: IconName;
  onBack?: () => void;
  action?: { label: string; onClick: () => void; icon: "bell" | "user" | "close" };
}

export function ScreenHeader({ title, subtitle, icon, onBack, action }: ScreenHeaderProps) {
  return (
    <header className="screen-header">
      {onBack ? (
        <button type="button" className="icon-button icon-button--soft" aria-label="Назад" onClick={onBack}><Icon name="back" /></button>
      ) : (
        <span className="brand-mark"><Icon name={icon ?? "home"} size={27} /></span>
      )}
      <div className="screen-header__text"><h1>{title}</h1>{subtitle && <p>{subtitle}</p>}</div>
      {action && <button type="button" className="icon-button" aria-label={action.label} title={action.label} onClick={action.onClick}><Icon name={action.icon} /></button>}
    </header>
  );
}
