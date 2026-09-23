export interface ImageItem {
  id: string;
  url: string;
}

interface ErrorResponse {
  detail?: string;
  message?: string;
}

export async function getNextImage(current?: string): Promise<ImageItem> {
  const query = current ? `?current_id=${encodeURIComponent(current)}` : "";
  const response = await fetch(`/images/get_next${query}`, {
    headers: { Accept: "application/json" },
  });

  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as ErrorResponse;
    throw new Error(body.message ?? body.detail ?? `Сервер ответил с кодом ${response.status}`);
  }

  return (await response.json()) as ImageItem;
}
