interface MaxWebApp {
  initData?: string;
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

export async function requestMaxContact(): Promise<{ phone: string; authDate: string; hash: string }> {
  if (!window.WebApp?.requestContact) throw new Error("Подтверждение номера доступно внутри MAX");
  return window.WebApp.requestContact();
}
