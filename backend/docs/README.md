# Структура и запуск

Требуется Python 3.12. Команды ниже выполняются из корня репозитория.

```sh
python -m pip install -r backend/requirements.txt
python -m pip install -r backend/tools/codegen/requirements.txt
cd backend
python -m src.bot.main
python -m uvicorn src.main:app --reload
```

Точка запуска бота — `backend/src/bot/main.py`. Прямые команды выше нужны
для локальной разработки до настройки запуска через Docker.

## Каталоги

- `backend/src/bot/`: обработчики, клавиатуры, мидлвари и запуск бота.
- `backend/src/views/`: HTTP-обработчики и реестр маршрутов.
- `backend/src/gen/<domain_name>/api/<остаток пути ручки>.py`: модели запросов,
  параметров и ответов API; домен берётся из первого сегмента URL. (не редактируется руками)
- `backend/src/domain/<domain_name>/`: общие сценарии бота и API;
  разбивка по файлам по мере появления логики.
- `backend/src/gen/<domain_name>/internal/<name>.py`: генерируемые внутренние модели
  Pydantic и перечисления Enum. (не редактируется руками)
- `backend/src/clients/`: будущие клиенты внешних сервисов.
- `backend/src/db/`: модели БД, определения запросов, репозитории, сессии
  и миграции. Подключение БД пока не реализовано.
- `backend/src/core/`: настройки и логирование.
- `backend/src/common/dependencies.py`: будущие зависимости HTTP-обработчиков.
- `backend/src/utils/`: вспомогательные функции по необходимости.
- `miniapp/src/`: пока только каталоги под мини-приложение, без установки
  frontend-зависимостей и без реализации интерфейса.
- `backend/docs/<domain_name>/api/<name>.yaml`: документы отдельных HTTP-ручек.
- `backend/docs/<domain_name>/internal/<name>.yaml`: группы связанных внутренних
  схем; каждый файл даёт один модуль `src/gen/<domain_name>/internal/<name>.py`.
- `backend/docs/example_codegen/`: учебные контракты для проверки генератора;
  это не предметная область продукта.
- `backend/tools/codegen/`: сборщик контрактов, генератор и шаблоны Python-кода.

Миниапп предполагает React, TypeScript, Vite, MAX Bridge и MAX UI.
`pages/` содержит экраны; `features/<domain_name>/ui/` — предметные компоненты;
`features/<domain_name>/integrations/{client_api,max_actions}.ts` — запросы
к бэкенду и предметные действия с MAX. Общий адаптер MAX — `integrations/max/`,
основа HTTP-клиента — `shared/base_http_client.ts`, общие компоненты —
`shared/common_ui/`.

## Зависимости

- `backend/requirements.txt` — полный `pip freeze` окружения бэкенда.
- `backend/tools/codegen/requirements.txt` — полный `pip freeze` отдельного окружения
  кодогенератора.

Правила YAML и команды генерации описаны в [codegen.md](codegen.md).

Проверки из каталога `backend/`: `python -m unittest discover -s tests -v`.
