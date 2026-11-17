# Отчёт DZ4. Агент-обёртка над User Directory API

История сборки, а не итоговая сводка. Ниже — какие промпты уходили в модель целиком, что она поняла не так и что в итоге запускается командой `python main.py`.

## Что собиралось

Минимальный агент на LangChain 1.5 (`create_agent`). Он принимает фразу на естественном языке, выбирает одну операцию каталога пользователей и вызывает её через единственный tool. Mock API — свой HTTP-сервис в этом репозитории, четыре операции: создать пользователя, получить по id, обновить поля или статус, получить список.

Каталог пишется в `data/users.json`, поэтому следующий запуск CLI видит уже созданного пользователя. Файл в `.gitignore`.

## LLM и настройка

Облачного ключа в окружении не было. Локальной Ollama тоже не было, поэтому поставлен Ollama 0.40.16.

`ollama pull qwen2.5:3b` дважды упал ещё на манифесте:

```text
Error: pull model manifest: Get "https://registry.ollama.ai/v2/library/qwen2.5/manifests/3b": net/http: TLS handshake timeout
```

Проверка TLS: `registry.ollama.ai` не завершает handshake, `huggingface.co` открывается. Модель взята оттуда: `bartowski/Qwen2.5-3B-Instruct-GGUF`, файл `Qwen2.5-3B-Instruct-Q4_K_M.gguf` (около 1.9 ГБ), и импортирована в Ollama под тем же именем `qwen2.5:3b`.

Шаблон взят со страницы библиотечной модели `qwen2.5` и поправлен в одной строке. В Ollama 0.40 `{{ .Function }}` печатает Go-структуру, а не JSON (это открытый дефект шаблона). В Modelfile стоит `{{ json .Function }}`, иначе модель видит кривую схему tool и путает имя функции.

Рабочий `.env` (секретов нет, ключ пустой):

```text
LLM_PROVIDER=ollama
OLLAMA_MODEL=qwen2.5:3b
OLLAMA_BASE_URL=http://127.0.0.1:11434
API_BASE_URL=http://127.0.0.1:8765
LLM_TEMPERATURE=0
```

В коде ещё зафиксированы `num_ctx=4096`, `num_predict=512`, `seed=42`. Облачный провайдер остаётся запасным путём: `LLM_PROVIDER=openai` и `OPENAI_API_KEY` в `.env`, образец в `.env.example`.

Запуск из Docker. Compose поднимает Ollama и контейнер агента, `main.py` внутри контейнера сам стартует mock API.

```powershell
docker compose run --rm agent "создай пользователя с именем Alex"
```

Локально без Docker:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env
ollama pull qwen2.5:3b
.\.venv\Scripts\python.exe main.py "создай пользователя с именем Alex"
```

`main.py` сам поднимает API на `127.0.0.1:8765`, если порт ещё свободен.

Модель 3B на CPU отвечает за 10–20 секунд на запрос. Для tool calling этого хватило, для аккуратного копирования JSON — нет. Это и есть основная развилка ниже.

## API

Сервис `api/server.py`, память процесса плюс JSON-файл.

| operation | HTTP | Смысл |
| --- | --- | --- |
| `create_user` | `POST /users` | Создать. Обязателен `name`, по желанию `email` и `role`. Статус всегда `active`. |
| `get_user` | `GET /users/{id}` | Карточка по id. |
| `update_user` | `PATCH /users/{id}` | Имя, email, роль или статус `active` / `blocked`. |
| `list_users` | `GET /users` | Список, опционально `?status=`. |

Роли: `member` (по умолчанию), `admin`, `viewer`.

До агента API прогнан напрямую: создание Alex, чтение, блокировка, список, фильтр `active`, 404 на id 9, 400 без имени. Все шесть ответов совпали с кодом сервера. Агент к этому слою ходит только по HTTP.

## Tool

Один tool `user_directory_api` в `tools/api_tool.py`, строки 43–126. Параметр `operation` выбирает метод. HTTP-вызов — `httpx`, функция `_call`, строки 21–40, сам запрос на строке 29. Результат tool печатается в консоль:

- строки 23–26: метод, URL и тело запроса;
- строка 39: JSON ответа `{"http_status": ..., "body": ...}`.

Пустые и `null` аргументы приводятся к пустой строке на строках 62–66. Это появилось после неудачного прогона, где модель прислала `null` и Pydantic отверг вызов ещё до HTTP.

## Попытка 1. Короткий промпт

Файл `prompts/system_v1.md`, текст целиком:

```text
Ты помощник. У тебя есть tool user_directory_api для работы с пользователями. Отвечай кратко и по делу.
```

Запрос: `создай пользователя с именем Alex`.

Tool вызвался правильно, HTTP тоже:

```text
[tool:user_directory_api] HTTP POST http://127.0.0.1:8765/users body={'name': 'Alex', 'role': 'member'}
[tool:user_directory_api] result {"http_status": 201, "body": {"user": {"id": 1, "name": "Alex", "email": "", "role": "member", "status": "active"}}}
[trace] ai tool_calls=[{'name': 'user_directory_api', 'args': {'operation': 'create_user', 'name': 'Alex', 'role': 'member'}, ...}]
```

Ответ модели контракту не соответствовал:

```text
Status: created
Action: User Alex was created successfully
Data: User ID: 1, Name: Alex, Role: member, Status: active
Errors: []
```

Что понято не так: статус `created` вместо `success` / `error`, Data текстом, Errors списком, язык английский, лишняя роль `member`, которой в запросе не было. Модель умеет выбрать tool и без жёстких границ, но формат ответа из воздуха не берёт.

Пользователь к этому моменту уже записался в `data/users.json`. Перед следующими сериями файл удалялся, чтобы id снова начинались с 1.

## Попытка 2. Жёсткий контракт без примера

Системный промпт стал описанием роли оператора API, границ и четырёх строк. Пользовательский текст оборачивался шаблоном. На серии из шести фраз маршрутизация была верной, HTTP — тоже:

| Запрос | operation | HTTP |
| --- | --- | --- |
| создай пользователя с именем Alex | `create_user` | `POST /users` → 201 |
| покажи пользователя с id 1 | `get_user` | `GET /users/1` → 200 |
| заблокируй пользователя с id 1 | `update_user` | `PATCH /users/1` `{"status":"blocked"}` → 200 |
| покажи список всех пользователей | `list_users` | `GET /users` → 200 |
| какая сегодня погода в Москве? | tool не вызывался | — |
| покажи пользователя с id 999 | `get_user` | `GET /users/999` → 404 |

Типичный черновик модели при этом:

```text
Status: success

Action: created user Alex

Data: {}

Errors: none
```

Между строками пустые линии, Action иногда на английском, Data всегда `{}`. Модель подтверждает успех и выбрасывает тело ответа. Для обёртки над API это провал: контракт требует результат вызова, а не пустой объект.

Погода отсеклась правильно: tool не вызывался, ответ `Status: error` и фраза про зону ответственности.

## Попытка 3. Пример JSON в системном промпте

Гипотеза: 3B не копирует `body`, потому что ни разу не видела образец. В системный промпт добавлен готовый пример успешного `create_user`, а в пользовательский шаблон — запрет оставлять Data пустым. Текст этого промпта в отчёт не входит.

Стало хуже. Пример перетянул внимание с вызова на форму ответа.

- «создай пользователя с именем Alex» — в tool ушли `name`, `role=member`, `status=active` и не ушёл обязательный `operation`. До HTTP дело не дошло: `operation: Field required`. Модель ещё и назвала это «запрос вне зоны ответственности».
- «покажи пользователя с id 1» — operation был верный, но рядом приехали `status/role/email/name: null`. Схема ждала строки, вызов отклонён до HTTP: `Input should be a valid string`.
- «заблокируй пользователя с id 1» — `update_user` и `PATCH` собрались правильно, но пользователя не было, потому что создание выше не прошло. API честно вернул 404 `user 1 not found`. Модель снова подписала это как чужую зону ответственности.
- «покажи список» — `list_users` и `GET /users` верные, каталог пустой. Data в ответе модели опять `{}`.
- Погода — tool не вызывался, это верно, но Data модель написала как `none`, а не `{}`.
- id 999 — снова `null` в строковых полях, HTTP не было.

Один контрольный запрос между попытками, ещё со старым каталогом, показал промежуточный вариант: `get_user` дошёл до HTTP, Data уже не пустая, но модель вынула внутренний объект пользователя и отбросила обёртку `{"user": ...}`. Плюс в аргументы попал лишний `status=active`, хотя читали карточку, а не меняли статус. Tool его проигнорировал, запрос был `GET /users/1`.

Вывод из попытки: пример на 3B учит копировать образец и додумывать поля (`status: active`, `role: member`, `null`), а не переносить фактический JSON. Промпт с примером откатан. В схему tool добавлено принятие `null`, чтобы такой вызов хотя бы доходил до HTTP.

## Финальный промпт

В системном промпте оставлены роль, границы и правила выбора `operation`. Примера JSON нет. Добавлена одна строка: не передавать `null` и не заполнять поля, которых нет в запросе.

Исполняемый системный промпт, `prompts/system.md`, целиком:

```text
Ты — оператор API сервиса User Directory. Ты не помощник общего назначения.
Единственная задача: понять запрос на естественном языке, при необходимости один раз вызвать tool `user_directory_api` и вернуть результат строго по контракту ниже.

Границы ответственности:
- Разрешены только операции каталога пользователей: создать пользователя, получить пользователя по id, обновить имя, email, роль или статус, получить список.
- Не выдумывай пользователей, id, email и статусы. Любое поле в Data бери только из JSON, который вернул tool.
- Если запрос не про каталог пользователей этого сервиса (погода, вычисления, код, общие знания, другие API) — tool не вызывай.
- Если для операции не хватает обязательного аргумента, tool не вызывай. Для create_user обязательно имя. Для get_user и update_user обязателен числовой id. Имя нельзя подменять id и нельзя угадывать id по имени.
- Один пользовательский запрос — не больше одного вызова tool.
- Статус бывает только `active` или `blocked`. Роль бывает только `member`, `admin` или `viewer`. Новый пользователь всегда создаётся со статусом active; статус при создании не передавай.
- Если пользователь просит «заблокировать», это update_user со status=blocked. Если просит «разблокировать» или «активировать», это update_user со status=active.
- Не передавай аргументы со значением null. Поля, которых нет в запросе, опускай.

Как выбирать operation:
- создать, добавь, зарегистрируй пользователя → create_user. Передай name и, если они есть в запросе, email и role. user_id не передавай.
- покажи, найди, открой пользователя по id → get_user. Передай только user_id.
- измени, переименуй, смени роль, заблокируй, разблокируй → update_user. Передай user_id и только изменяемые поля.
- список, все пользователи, кто есть, сколько пользователей, фильтр по статусу → list_users. status передавай только когда просят фильтр active или blocked.

Контракт ответа. Верни ровно четыре строки подряд, без пустых строк, без markdown и без текста до или после:

Status: success | error
Action: <одна короткая фраза, что сделано или почему отказано>
Data: <JSON тела API в одну строку, либо {} если tool не вызывался>
Errors: <текст ошибки или none>

Правила полей:
- Status: success — только если tool вернул http_status 200 или 201.
- Status: error — если tool не вызывался, если http_status не 2xx, или если запрос вне зоны ответственности.
- Action пиши по-русски.
- Data — это объект body из ответа tool, одной строкой. Не копируй обёртку http_status. Если вызова не было, напиши {}. Не оставляй Data пустым и не пиши none.
- Errors: none при success. Иначе коротко укажи причину: текст message из API, нехватку поля или фразу «запрос вне зоны ответственности User Directory».
```

Пользовательский шаблон, `prompts/user_template.md`, целиком. `{query}` подставляет фразу из CLI:

```text
Запрос оператора сервиса:
{query}

Если запрос относится к каталогу пользователей User Directory, вызови tool user_directory_api не больше одного раза.
Затем верни ответ строго четырьмя строками контракта из системного промпта: Status, Action, Data, Errors.
Не вставляй пустые строки между полями. В Data скопируй объект body из ответа tool.
```

Контракт описан в `prompts/system.md`, строки 20–32, и в `README.md`, раздел «Контракт ответа», строки 72–85.

Модель по-прежнему пишет `Data: {}` и иногда копирует заголовок шаблона буквально (`Status: success | error`). Поэтому CLI не отдаёт черновик как есть. `build_contract` в `main.py`, строки 143–167, собирает четыре строки из фактического ответа tool: `success` только при HTTP 200 или 201, в `Data` кладётся объект `body` без пересказа. Action для успешного вызова строится по `operation`. Если tool не вызывался, статус `error`, Data `{}`. Черновик модели остаётся в строке `[trace] model_draft`, чтобы было видно, где она ошиблась.

## Финальная проверка

Каталог перед серией очищен. Шесть запусков `python main.py "<запрос>"` подряд, один и тот же процесс API не держался: каждый процесс поднимал сервер сам, состояние жило в файле.

### 1. создай пользователя с именем Alex → `create_user`

Ожидаемый метод: `create_user`, `POST /users`.

```text
[tool:user_directory_api] HTTP POST http://127.0.0.1:8765/users body={'name': 'Alex'}
[tool:user_directory_api] result {"http_status": 201, "body": {"user": {"id": 1, "name": "Alex", "email": "", "role": "member", "status": "active"}}}
[trace] ai tool_calls=[{'name': 'user_directory_api', 'args': {'operation': 'create_user', 'name': 'Alex'}, 'id': 'ab9f20b5-b644-411b-b7fa-ed514ea3632d', 'type': 'tool_call'}]
[trace] model_draft: Status: success | Action: User created successfully | Data: {} | Errors: none
Status: success
Action: создал пользователя Alex
Data: {"user": {"id": 1, "name": "Alex", "email": "", "role": "member", "status": "active"}}
Errors: none
```

Черновик снова с пустым Data и английским Action. В контракт попало тело API.

### 2. покажи пользователя с id 1 → `get_user`

```text
[tool:user_directory_api] HTTP GET http://127.0.0.1:8765/users/1 body=None
[tool:user_directory_api] result {"http_status": 200, "body": {"user": {"id": 1, "name": "Alex", "email": "", "role": "member", "status": "active"}}}
[trace] ai tool_calls=[{'name': 'user_directory_api', 'args': {'operation': 'get_user', 'user_id': 1}, 'id': 'd72db309-b33b-46ef-bf46-f71389b0ea38', 'type': 'tool_call'}]
[trace] model_draft: Status: success | error | Action: показал пользователя с id 1 | Data: {} | Errors: none
Status: success
Action: получил пользователя с id 1
Data: {"user": {"id": 1, "name": "Alex", "email": "", "role": "member", "status": "active"}}
Errors: none
```

Модель скопировала из промпта кусок `success | error` как значение Status. Вызов tool при этом был точный: только `get_user` и `user_id=1`.

### 3. заблокируй пользователя с id 1 → `update_user`

```text
[tool:user_directory_api] HTTP PATCH http://127.0.0.1:8765/users/1 body={'status': 'blocked'}
[tool:user_directory_api] result {"http_status": 200, "body": {"user": {"id": 1, "name": "Alex", "email": "", "role": "member", "status": "blocked"}}}
[trace] ai tool_calls=[{'name': 'user_directory_api', 'args': {'operation': 'update_user', 'user_id': 1, 'status': 'blocked'}, 'id': '2c40f3e4-2445-4073-b573-7ce8fc94f2ca', 'type': 'tool_call'}]
[trace] model_draft: Status: success | Action: заблокировал пользователя с id 1 | Data: {} | Errors: none
Status: success
Action: заблокировал пользователя с id 1
Data: {"user": {"id": 1, "name": "Alex", "email": "", "role": "member", "status": "blocked"}}
Errors: none
```

Здесь черновик Action уже по-русски и по делу. Data модель опять обнулила, в контракте статус `blocked` из API.

### 4. покажи список всех пользователей → `list_users`

```text
[tool:user_directory_api] HTTP GET http://127.0.0.1:8765/users body=None
[tool:user_directory_api] result {"http_status": 200, "body": {"users": [{"id": 1, "name": "Alex", "email": "", "role": "member", "status": "blocked"}], "count": 1}}
[trace] ai tool_calls=[{'name': 'user_directory_api', 'args': {'status': None, 'operation': 'list_users'}, 'id': 'df7cb291-b8bc-46cd-add9-fa361b29899b', 'type': 'tool_call'}]
[trace] model_draft: Status: success | Action: showed list of users | Data: {} | Errors: none
Status: success
Action: получил список пользователей, count=1
Data: {"users": [{"id": 1, "name": "Alex", "email": "", "role": "member", "status": "blocked"}], "count": 1}
Errors: none
```

`status: None` до HTTP не дошёл как фильтр: tool превращает `null` в пустую строку и зовёт `GET /users` без query. В списке тот же Alex, уже `blocked`. Цепочка из четырёх команд сохранила состояние.

### 5. какая сегодня погода в Москве? → tool не вызывается

```text
[trace] model_draft: {"name": "user_directory_api", "arguments": {"operation": "get_user", "user_id": 1}}
Status: error
Action: запрос вне зоны ответственности User Directory
Data: {}
Errors: запрос вне зоны ответственности User Directory
```

HTTP не было. Модель не сделала tool call, а напечатала JSON вызова текстом, да ещё и `get_user` для вопроса о погоде. Такой текст не исполняется. Контракт — отказ. Это как раз тот случай, где граница полезнее, чем «послушать модель».

### 6. покажи пользователя с id 999 → `get_user`, ошибка API

```text
[tool:user_directory_api] HTTP GET http://127.0.0.1:8765/users/999 body=None
[tool:user_directory_api] result {"http_status": 404, "body": {"error": "not_found", "message": "user 999 not found"}}
[trace] ai tool_calls=[{'name': 'user_directory_api', 'args': {'operation': 'get_user', 'user_id': 999}, 'id': 'aca01413-f4c0-439b-9689-7fa50ceeae68', 'type': 'tool_call'}]
[trace] model_draft: Status: error | Action: failed to find user | Data: {} | Errors: user 999 not found
Status: error
Action: не выполнил get_user: user 999 not found
Data: {"error": "not_found", "message": "user 999 not found"}
Errors: user 999 not found
```

Реальных вызовов API в серии пять (запросы 1–4 и 6). Запрос 5 tool не трогал.

## Выводы

Сработало:

- Один tool с полем `operation` и четырьмя HTTP-методами. 3B по фразам «создай / покажи по id / заблокируй / список / нет такого id» выбирает нужную операцию, если в промпте нет отвлекающего примера.
- Граница «не наш сервис» держится, пока вызов не оформлен как настоящий tool call. Текст, похожий на вызов, сам по себе API не трогает.
- Короткий промпт достаточен, чтобы попасть в tool, и недостаточен для контракта.
- Контракт надёжно получается, когда `Status` и `Data` собираются из ответа HTTP, а не из пересказа модели.

Не сработало:

- Просьба «скопируй body в Data». На `qwen2.5:3b` строка Data почти всегда `{}`, даже когда tool в том же трейсе вернул JSON.
- Пример удачного ответа в системном промпте. Модель начала дописывать `role` и `status`, забывать `operation` и слать `null`.
- Дословный шаблон `success | error`: один раз он уехал в ответ как значение поля Status.
- Реестр Ollama с этой машины. Обход — GGUF с Hugging Face и свой Modelfile.

Секреты в репозиторий не клались. `.env` в `.gitignore`, в `.env.example` ключ пустой.
