# Структура и запуск

Требуется Python 3.12. Команды ниже выполняются из корня репозитория.
Перед локальным запуском бэкенда заполните все `DB_*` в корневом `.env` и
используйте отдельный локальный PostgreSQL. В этом режиме замените `DB_HOST=db`
из образца на адрес локальной БД. База из Compose доступна только контейнерам,
не хосту.

```sh
python -m pip install -r backend/requirements.txt
python -m pip install -r backend/tools/codegen/requirements.txt
cd backend
alembic -c alembic.ini upgrade head
python -m src.bot.main
python -m uvicorn src.main:app --reload
```

Последние две команды запускайте в разных терминалах. Миграцию выполняйте
один раз после обновления кода, до запуска процессов.

Точка запуска бота — `backend/src/bot/main.py`. При запуске через
`docker compose up --build` бот работает в отдельном контейнере; прямые
команды выше нужны только для локальной разработки. Не запускайте локального
бота одновременно с контейнером, если они используют один токен.

## Каталоги

- `backend/src/bot/`: обработчики текстовых команд MAX и запуск бота с
  доставкой уведомлений. Команды доступа: `/access help`, карточки:
  `/issuehelp`, уведомления: `/notificationhelp`, общие черновики: `/drafthelp`,
  приватные вложения: `/filehelp`.
- `backend/src/views/`: HTTP-обработчики и сгенерированный реестр маршрутов.
- `backend/src/gen/<domain_name>/api/<остаток пути ручки>.py`: модели запросов,
  параметров и ответов API; домен берётся из первого сегмента URL. (не редактируется руками)
- `backend/src/domain/<domain_name>/`: общие сценарии бота и API;
  разбивка по файлам по мере появления логики.
- `backend/src/gen/<domain_name>/internal/<name>.py`: генерируемые внутренние модели
  Pydantic и перечисления Enum. (не редактируется руками)
- `backend/src/clients/`: будущие клиенты внешних сервисов.
- `backend/src/db/`: модели пяти схем PostgreSQL, сессии и Alembic-миграции.
- `backend/src/core/`: настройки и логирование.
- `backend/src/db/session.py`: `get_session` для передачи SQLAlchemy-сессии
  в HTTP-обработчик. Изменяющий сценарий фиксирует предметные данные и журнал
  в одной транзакции.
- `backend/src/utils/`: вспомогательные функции по необходимости.
- `miniapp/src/`: интерфейс React/TypeScript, MAX Bridge, интеграции с API;
  локальное демо включается только через `VITE_DEMO_MODE=true`.
- `backend/docs/<domain_name>/api/<name>.yaml`: документы отдельных HTTP-ручек.
- `backend/docs/<domain_name>/internal/<name>.yaml`: группы связанных внутренних
  схем; каждый файл даёт один модуль `src/gen/<domain_name>/internal/<name>.py`.
- `backend/docs/example_codegen/`: учебные контракты для проверки генератора;
  это не предметная область продукта.
- `backend/tools/codegen/`: сборщик контрактов, генератор и шаблоны Python-кода.

Мини-приложение использует React, TypeScript, Vite, MAX Bridge и собственные
стили; библиотека MAX UI пока не подключена.
`pages/` содержит экраны; `features/<domain_name>/ui/` — предметные компоненты;
`features/<domain_name>/integrations/{client_api,max_actions}.ts` — запросы
к бэкенду и предметные действия с MAX. Общий адаптер MAX — `integrations/max/`,
основа HTTP-клиента — `shared/base_http_client.ts`, общие компоненты —
`shared/common_ui/`.

## Зависимости

- `backend/requirements.txt` — версии пакетов для исполнения бэкенда и миграций;
  `SQLAlchemy[asyncio]` явно подтягивает `greenlet` для асинхронной работы с БД.
- `backend/tools/codegen/requirements.txt` — полный `pip freeze` отдельного окружения
  кодогенератора.

Правила YAML и команды генерации описаны в [codegen.md](codegen.md).
Потоковый API приватных файлов подключён отдельным маршрутизатором и описан
в [files/README.md](files/README.md); в статический `backend/openapi.yaml`
кодогенератора он пока не входит, но присутствует в живом `/openapi.json`.

Проверки из каталога `backend/`: `python -m unittest discover -s tests -v`.
