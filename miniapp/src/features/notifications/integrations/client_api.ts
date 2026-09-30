import { isDemoMode, type AppSnapshot } from "../../issues/integrations/client_api";
import type { Role } from "../../issues/types";
import { requestJson } from "../../../shared/base_http_client";

export interface AppNotification {
  id: string;
  subject_kind: string;
  subject_id: string;
  event_kind: string;
  created_at: string;
  read_at: string | null;
  bot_state: string;
}

export type NotificationAudience = Role | "support" | "admin";

const demoRead = new Set<string>();

function demoNotifications(role: Role, snapshot: AppSnapshot): AppNotification[] {
  const issues = role === "employee"
    ? snapshot.issues.filter((issue) => issue.status === "open" || issue.status === "reviewing")
    : snapshot.issues.filter((issue) => issue.status !== "open");
  return issues.slice(0, 3).map((issue, index) => ({
    id: `demo-notification-${role}-${issue.id}`,
    subject_kind: "issue_card",
    subject_id: issue.id,
    event_kind: index === 0 ? "issues.card.status_changed" : "issues.card.comment_added",
    created_at: issue.updatedAt,
    read_at: demoRead.has(`demo-notification-${role}-${issue.id}`) ? issue.updatedAt : null,
    bot_state: "sent",
  }));
}

export async function listAppNotifications(role: NotificationAudience, snapshot: AppSnapshot | null): Promise<AppNotification[]> {
  if (isDemoMode) return snapshot && (role === "resident" || role === "employee") ? demoNotifications(role, snapshot) : [];
  const response = await requestJson<{ items: AppNotification[] }>("/notifications/list");
  return response.items;
}

export async function markAppNotificationRead(id: string): Promise<void> {
  if (isDemoMode) { demoRead.add(id); return; }
  await requestJson<{ read: boolean }>("/notifications/mark_read", {
    method: "POST",
    body: JSON.stringify({ notification_id: id }),
  });
}
