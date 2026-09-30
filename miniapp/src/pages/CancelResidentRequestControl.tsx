import { useState } from "react";

import { requestAccessCancellation } from "../features/issues/integrations/access_actions_api";
import "./access-flows.css";

export function CancelResidentRequestControl({ id, onChanged }: { id: string; onChanged: () => Promise<void> }) {
  const [open, setOpen] = useState(false);
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function cancel() {
    if (!confirmed || busy) return;
    setBusy(true); setError("");
    try {
      await requestAccessCancellation("resident", id);
      setOpen(false); setConfirmed(false);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Не удалось отменить заявку");
      setBusy(false);
      return;
    }
    try { await onChanged(); }
    catch { setError("Заявка отменена, но список не обновился. Откройте его повторно."); }
    finally { setBusy(false); }
  }

  return <div className="access-cancel-control">
    {open ? <div className="access-cancel-control__confirm">
      <label className="checkbox-row"><input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} /><span>Подтверждаю отмену заявки на доступ к дому</span></label>
      <div className="button-row"><button type="button" className="button button--soft" disabled={busy} onClick={() => { setOpen(false); setConfirmed(false); setError(""); }}>Оставить заявку</button><button type="button" className="button button--primary" disabled={busy || !confirmed} onClick={() => void cancel()}>{busy ? "Отменяем…" : "Подтвердить отмену"}</button></div>
    </div> : <button type="button" className="button button--soft" onClick={() => setOpen(true)}>Отменить заявку</button>}
    {error && <p className="form-error" role="alert">{error}</p>}
  </div>;
}
