import { useEffect, useState } from "react";

import { issuesClient, type NotificationSubject } from "../../issues/integrations/client_api";
import { Icon } from "../../../shared/common_ui/Icon";

interface Props {
  subject: NotificationSubject;
  id: string;
  expanded?: boolean;
}

export function NotificationToggle({ subject, id, expanded = false }: Props) {
  const [open, setOpen] = useState(expanded);
  const [muted, setMuted] = useState<boolean | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!open) return;
    let active = true;
    setMuted(null);
    setError("");
    void issuesClient.getBotMute(subject, id)
      .then((value) => { if (active) setMuted(value); })
      .catch((reason) => { if (active) setError(reason instanceof Error ? reason.message : "Не удалось загрузить настройку"); });
    return () => { active = false; };
  }, [open, subject, id]);

  async function toggle() {
    if (muted === null) return;
    setBusy(true);
    setError("");
    try { setMuted(await issuesClient.setBotMute(subject, id, !muted)); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Не удалось изменить настройку"); }
    finally { setBusy(false); }
  }

  if (!open) return <button type="button" className="notification-link" onClick={() => setOpen(true)}><Icon name="bell" size={16} /> Уведомления по заявке</button>;

  return <div className="notification-setting"><div><strong>Сообщения бота</strong><small>{muted === null ? "Загружаем настройку…" : muted ? "Отключены для этого обращения" : "Включены для этого обращения"}</small></div><button type="button" className={`switch ${muted === false ? "switch--on" : ""}`} role="switch" aria-checked={muted === false} aria-label="Сообщения бота" disabled={busy || muted === null} onClick={() => void toggle()}><span /></button>{error && <p className="form-error" role="alert">{error}</p>}</div>;
}
