import { useEffect, useState } from "react";

import { acceptSupportInvitation, getMySupportInvitations, type SupportInvitation } from "../features/admin/integrations/client_api";
import { Icon } from "../shared/common_ui/Icon";

export function SupportInvitationNotice({ onAccepted }: { onAccepted: () => Promise<void> }) {
  const [invitations, setInvitations] = useState<SupportInvitation[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    void getMySupportInvitations()
      .then((items) => { if (!cancelled) setInvitations(items); })
      .catch((reason) => { if (!cancelled) setError(reason instanceof Error ? reason.message : "Не удалось проверить приглашения"); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  async function accept(id: string) {
    setBusy(true); setError("");
    try { await acceptSupportInvitation(id); await onAccepted(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Не удалось принять приглашение"); }
    finally { setBusy(false); }
  }

  if (loading || (invitations.length === 0 && !error)) return null;
  return <section className="panel support-invite-notice">
    <div><Icon name="shield" size={24} /><div><strong>Приглашение в поддержку</strong><p>Администратор предложил вам разбирать обращения УК и домов. Это не права администратора.</p></div></div>
    {invitations.map((invitation) => <button key={invitation.id} type="button" className="button button--primary" disabled={busy} onClick={() => void accept(invitation.id)}>Принять приглашение для {invitation.phone_number}</button>)}
    {error && <p className="form-error" role="alert">{error}</p>}
  </section>;
}
