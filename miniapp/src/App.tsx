import { useCallback, useEffect, useRef, useState } from "react";

import { getNextImage, type ImageItem } from "./features/images/api";


function preloadImage(url: string): Promise<void> {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => resolve();
    image.onerror = () => reject(new Error("Не удалось загрузить изображение"));
    image.src = url;
  });
}

export function App() {
  const [image, setImage] = useState<ImageItem | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const requestInProgress = useRef(false);
  const initialRequestStarted = useRef(false);

  const loadNextImage = useCallback(async () => {
    if (requestInProgress.current) {
      return;
    }

    requestInProgress.current = true;
    setIsLoading(true);
    setError(null);

    try {
      const nextImage = await getNextImage(image?.id);
      await preloadImage(nextImage.url);
      setImage(nextImage);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Неизвестная ошибка");
    } finally {
      requestInProgress.current = false;
      setIsLoading(false);
    }
  }, [image?.id]);

  useEffect(() => {
    if (initialRequestStarted.current) {
      return;
    }
    initialRequestStarted.current = true;
    void loadNextImage();
  }, [loadNextImage]);

  return (
    <main className="app-shell">
      <button
        className="image-button"
        type="button"
        onClick={() => void loadNextImage()}
        disabled={isLoading}
        aria-label="Показать следующую картинку"
      >
        {image ? <img className="image" src={image.url} alt="" /> : null}

        {!image && !error ? <span className="message">Загружаем картинку…</span> : null}
        {error ? <span className="message message--error">{error}<br />Нажми, чтобы повторить</span> : null}
        {image && isLoading ? <span className="loader" aria-label="Загрузка" /> : null}
      </button>
    </main>
  );
}
