# Контракты и кодогенерация

Документы ручек и внутренних схем служат источником Python-моделей
и заготовок HTTP-обработчиков.

## Команды

Из каталога `backend/`, с активированным рабочим Python-окружением.
Все пути ниже указаны относительно `backend/`:

```sh
python -m tools.codegen
```

Эта одна команда выполняет полный цикл: чтение YAML → сборка и валидация
OpenAPI → генерация в памяти → проверка синтаксиса, существующих обработчиков
и допустимости перезаписи → запись изменившихся файлов. При ошибке валидации
генерация и запись не начинаются. Отдельно запускать проверки не требуется.

Необязательные флаги:

- `--skip-validation`: пропустить проверку OpenAPI. Разбор YAML, разрешение
  ссылок, ограничения поддерживаемого формата, проверка синтаксиса Python
  и защита ручных файлов остаются: без них генерация не может быть корректной.
  Флаг имеет смысл, если контракт уже проверен; фактическое ускорение зависит
  от размера схем.
- `--check`: выполнить тот же цикл, но только сравнить результат с файлами,
  ничего не записывая. Это отдельный режим для проверки актуальности, например
  в CI, а не обязательный шаг после генерации. Код возврата 1 означает различия,
  0 — отсутствие различий, 2 — ошибку. Совместим с `--skip-validation`.
- `--help`: показать справку.

OpenAPI собирается в `openapi.yaml` в корне `backend/`, вручную этот файл
не редактируется.
Сборка использует OpenAPI 3.1 и `openapi-spec-validator`; модели генерируются
через `datamodel-code-generator`, обработчики и реестр — по шаблонам Jinja2.

## Документ ручки

Один файл `docs/<domain_name>/api/<name>.yaml` описывает одну операцию.
Имя файла и домен документа не задают выходной путь: он определяется `path`.
Домен документа используется как тег OpenAPI по умолчанию; явное поле `tags`
в YAML заменяет это значение.
Ниже пример формата, а не утверждённый предметный API:

```yaml
# docs/sample/api/get_request.yaml
path: /sample/client/get_request
method: get
summary: Получить заявку
description: Возвращает заявку по идентификатору.
parameters:
  - name: request_id
    in: query
    required: true
    schema:
      type: integer
      minimum: 1
responses:
  '200':
    description: Заявка найдена.
    content:
      application/json:
        schema:
          $ref: '#/schemas/Response200'
  '404':
    description: Заявки с таким идентификатором нет.
    content:
      application/json:
        schema:
          $ref: '#/schemas/Response404'
schemas:
  Response200:
    type: object
    required: [id, status]
    properties:
      id:
        type: integer
      status:
        $ref: '../internal/request_data.yaml#/schemas/RequestStatus'
  Response404:
    type: object
    required: [code, message]
    properties:
      code:
        type: string
        const: request_not_found
      message:
        type: string
```

В примере GET выше входной `request_id` передаётся в URL:
`/sample/client/get_request?request_id=123`. Тела запроса там нет.
Для передачи JSON в теле рассмотрим отдельную POST-ручку:

```yaml
path: /sample/client/create_request
method: post
requestBody:
  required: true
  content:
    application/json:
      schema:
        $ref: '#/schemas/Request'
responses:
  '201':
    description: Заявка создана.
    content:
      application/json:
        schema:
          $ref: '#/schemas/Response201'
schemas:
  Request:
    type: object
    required: [title]
    properties:
      title:
        type: string
        minLength: 1
  Response201:
    type: object
    required: [id]
    properties:
      id:
        type: integer
```

Клиент отправляет тело `{"title": "Не работает принтер"}` с заголовком
`Content-Type: application/json`. Обёртку `Request` в JSON добавлять не нужно.

- `requestBody` описывает тело входящего HTTP-запроса.
- Его `required: true` означает, что само тело обязательно.
- `content` перечисляет допустимые форматы тела; здесь только `application/json`.
- `schema` задаёт структуру данных для этого формата.
- `$ref: '#/schemas/Request'` означает «использовать схему Request из раздела
  schemas текущего YAML»: `#` — текущий документ, далее путь внутри него.
- `schemas.Request.required: [title]` отдельно требует поле `title` внутри тела.

`Request` — выбранное нами имя модели, не специальное слово протокола.
Генератор создаст Python-класс и подключит его в аргумент `body` обработчика.
FastAPI прочитает JSON и проверит данные до вызова обработчика.

`path`, `method`, `schemas` — поля нашего формата сборки. Остальные поля
соответствуют объекту операции OpenAPI. Локальные ссылки имеют вид
`#/schemas/<имя>`, ссылки на внутренние схемы — относительный путь к YAML
с указанием схемы: `file.yaml#/schemas/<имя>`.
Неиспользуемые схемы также генерируются. `QueryParams` зарезервирован:
он строится из списка `parameters` автоматически.

## Внутренние схемы

Один YAML — группа связанных именованных схем.
В пути `docs/<domain_name>/internal/<name>.yaml` каталог `<domain_name>`
определяет домен, имя файла — Python-модуль. Ключи в `schemas` задают имена классов.
В одном файле можно описать модели, перечисления и ссылки между ними.

```yaml
# docs/sample/internal/request_data.yaml
schemas:
  RequestStatus:
    type: string
    enum: [new, closed]
  RequestData:
    type: object
    required: [id, status]
    properties:
      id:
        type: integer
      status:
        $ref: '#/schemas/RequestStatus'
```

Результат: один модуль `src/gen/sample/internal/request_data.py` с классами
`RequestStatus` и `RequestData`. Имя файла не обязано совпадать с именем
одной из схем: например, `models.yaml` даст модуль `models.py`.
Вложенные анонимные структуры могут образовать вспомогательные классы
в том же модуле.

На тип из другого файла ссылаемся как `./other.yaml#/schemas/TypeName`.
Из API к внутренним схемам своего домена: `../internal/other.yaml#/schemas/TypeName`.
К внутренним схемам другого домена: `../../other_domain/internal/other.yaml#/schemas/TypeName`.
Генератор связывает схемы и формирует импорты между модулями. Одинаковые имена
классов допускаются в разных модулях. Внутренние схемы не могут ссылаться
на HTTP-схемы.

## Ручной и генерируемый код

В `src/gen` модели сгруппированы сначала по домену, затем по назначению:
`internal/` для внутренних структур и `api/` для HTTP-контрактов.
Для API домен — первый сегмент URL, остальные сегменты идут после `api/`.
Для внутренних структур домен берётся из `docs/<domain_name>/internal/`.

Для условного пути `/sample/client/get_request` создаются:

- `src/views/sample/client/get_request.py`: асинхронная функция;
  создаётся только при отсутствии файла. Входные модели подключены в сигнатуре.
- `src/gen/sample/api/client/get_request.py`: модели запроса, query-параметров,
  успешных ответов и ошибок. Этот файл полностью генерируемый.
- `src/views/_generated_router.py`: подключает функции обработчиков
  к FastAPI, устанавливает путь, метод, модели ответов и описания.

Регистрация маршрута вынесена из ручного обработчика, чтобы обновление
контракта не требовало перезаписывать его реализацию. Существующий обработчик
проверяется на наличие ожидаемой async-функции и входных моделей в сигнатуре.

Обработчик использует абсолютный импорт
`from src.gen.sample.api.client import get_request as models`.
Между генерируемыми моделями тоже используются абсолютные импорты из `src.gen`.
Запуск приложения и тестов выполняется из `backend/`, где доступен пакет `src`.
Сервис импортирует внутренние
модели, например
`from src.gen.sample.internal.request_data import RequestData`,
но не HTTP-модели. Преобразование внутреннего результата в HTTP-ответ выполняет
обработчик. Общий код сервиса доступен и HTTP-ручкам, и обработчикам бота.

Заглушка возвращает HTTP 501 до написания реализации. После реализации
обработчик возвращает одну из моделей ответа, объявленных в YAML:

```python
async def get_request(...) -> models.Response200 | models.Response404 | Response:
    result = await service.get_request(...)
    if result is None:
        return models.Response404(
            code="request_not_found",
            message="Заявка не найдена",
        )
    return models.Response200(id=result.id, status=result.status)
```

Сгенерированный реестр строит для каждой ручки таблицу «тип модели → HTTP-код»
из раздела `responses`. Обёртка вызывает обработчик, определяет код по типу
возвращённой модели и сериализует её в JSON. Название `Response404` само по себе
не задаёт статус: источником остаётся ключ `'404'` в YAML. Одна модель не может
быть назначена нескольким кодам одной ручки, потому что тогда код нельзя выбрать
по её типу.

Объект `Response` пропускается без изменений для ответов без тела, файлов,
перенаправлений, специальных заголовков и других нестандартных случаев.
Возврат модели, не объявленной в `responses`, считается ошибкой обработчика.

Ручные обработчики и существующие `__init__.py` не удаляются. Если убрана ручка,
её регистрация исчезает, но файл обработчика остаётся для отдельного решения.
Файлы вне списка не удаляются. Если файл из списка изменили вручную, генератор
останавливается до записи/удаления: изменения нужно разобрать отдельно.
На отсутствующую схему нельзя оставлять `$ref`: это ошибка до изменения файлов.
`--check` показывает предстоящие удаления, но ничего не изменяет.
