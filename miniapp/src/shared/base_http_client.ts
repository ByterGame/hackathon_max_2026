import { getSignedMaxInitData } from "../integrations/max/bridge";

export async function requestResponse(path: string, init: RequestInit = {}): Promise<Response> {
  const url = new URL(path, window.location.origin);
  if (url.origin !== window.location.origin) throw new Error("Запросы разрешены только к серверу приложения");
  const signedInitData = getSignedMaxInitData();
  if (!signedInitData) throw new Error("Откройте сервис в MAX для входа");

  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  headers.set("X-Max-Init-Data", signedInitData);
  if (typeof init.body === "string" && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  if (!["GET", "HEAD", "OPTIONS"].includes((init.method ?? "GET").toUpperCase()) && !headers.has("Idempotency-Key")) {
    headers.set("Idempotency-Key", crypto.randomUUID());
  }

  let response: Response;
  try { response = await fetch(url, { ...init, headers }); }
  catch { throw new Error("Нет связи с сервером. Попробуйте позже"); }

  if (!response.ok) {
    const body = await response.json().catch(() => ({})) as { detail?: string | { message?: string }; message?: string };
    const detail = typeof body.detail === "string" ? body.detail : body.detail?.message;
    throw new Error(body.message ?? detail ?? `Ошибка сервера: ${response.status}`);
  }
  return response;
}

export async function requestJson<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await requestResponse(path, init);
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}
