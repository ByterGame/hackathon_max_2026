interface MaxWebApp {
  initData?: string;
  initDataUnsafe?: { start_param?: string };
  requestContact?: () => Promise<{ phone: string; authDate: string; hash: string }>;
}

declare global {
  interface Window {
    WebApp?: MaxWebApp;
  }
}

export function getSignedMaxInitData(): string {
  return window.WebApp?.initData ?? "";
}

export function getLaunchIssueId(): string | null {
  const startParam = window.WebApp?.initDataUnsafe?.start_param;
  const startMatch = /^issue_([a-f\d]{32})$/i.exec(startParam ?? "");
  if (startMatch) {
    const value = startMatch[1].toLowerCase();
    return `${value.slice(0, 8)}-${value.slice(8, 12)}-${value.slice(12, 16)}-${value.slice(16, 20)}-${value.slice(20)}`;
  }
  const queryIssue = new URLSearchParams(window.location.search).get("issue");
  return /^[a-f\d]{8}-[a-f\d]{4}-[a-f\d]{4}-[a-f\d]{4}-[a-f\d]{12}$/i.test(queryIssue ?? "") ? queryIssue : null;
}

export async function requestMaxContact(): Promise<{ phone: string; authDate: string; hash: string }> {
  if (!window.WebApp?.requestContact) throw new Error("Подтверждение номера доступно внутри MAX");
  return window.WebApp.requestContact();
}
