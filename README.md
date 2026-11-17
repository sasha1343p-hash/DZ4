# DZ4 — natural-language обёртка над User Directory API

Минимальный агент на LangChain. Он принимает запрос на естественном языке, вызывает mock API каталога пользователей через один tool и отвечает фиксированным контрактом.

## Что умеет API

Сервис `User Directory` живёт в `api/server.py`. Каталог пишется в `data/users.json` и переживает перезапуск процесса. Чтобы начать с пустого каталога, удалите этот файл. Он в `.gitignore`.

| Операция | HTTP | Назначение |
| --- | --- | --- |
| `create_user` | `POST /users` | Создать пользователя (`name`, необязательно `email` и `role`) |
| `get_user` | `GET /users/{id}` | Получить пользователя по id |
| `update_user` | `PATCH /users/{id}` | Обновить имя, email, роль или статус (`active` / `blocked`) |
| `list_users` | `GET /users` | Список пользователей, опционально `?status=` |

Роли: `member` (по умолчанию), `admin`, `viewer`. Новый пользователь получает статус `active`.

## LLM

По умолчанию — локальная модель через [Ollama](https://ollama.com/). Облачный OpenAI-совместимый провайдер тоже поддерживается. Секрет кладётся только в `.env`, который не коммитится.

## Настройка

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env
```

Локальная модель:

```powershell
ollama pull qwen2.5:3b
```

Ollama должна быть запущена (`ollama serve` или приложение Ollama). В `.env` оставьте `LLM_PROVIDER=ollama`.

Если `ollama pull` падает с `TLS handshake timeout` на `registry.ollama.ai`, скачайте GGUF `Qwen2.5-3B-Instruct` Q4_K_M с Hugging Face и соберите локальную модель с тем же именем: `ollama create qwen2.5:3b -f Modelfile`. В шаблоне tool-calling строка схемы должна быть `{{ json .Function }}`, иначе Ollama 0.40 печатает Go-структуру вместо JSON.

OpenAI-совместимый провайдер: в `.env` укажите `LLM_PROVIDER=openai`, `OPENAI_API_KEY` и при необходимости `OPENAI_BASE_URL` / `OPENAI_MODEL`.

## Запуск в Docker

Нужны Docker Desktop и запущенный демон. Compose поднимает Ollama и контейнер агента. Mock API стартует внутри контейнера.

```powershell
docker compose run --rm agent "создай пользователя с именем Alex"
```

Первый запуск скачивает образы и модель `qwen2.5:3b`. Если реестр Ollama не открывается, положите GGUF `Qwen2.5-3B-Instruct` Q4_K_M и укажите путь в `.env`:

```text
GGUF_FILE=C:/path/to/qwen2.5-3b-instruct-q4_k_m.gguf
```

Тогда `docker/ensure-model.sh` соберёт модель из файла через `docker/Modelfile`. Каталог пользователей монтируется из `./data`.

## Запуск без Docker

Одной командой. `main.py` сам поднимает mock API на `http://127.0.0.1:8765`, если он ещё не запущен.

```powershell
.\.venv\Scripts\python.exe main.py "создай пользователя с именем Alex"
```

Отдельный запуск API, если нужно смотреть его логи в другом окне:

```powershell
.\.venv\Scripts\python.exe -m api.server
```

## Контракт ответа

Требования заданы в `prompts/system.md`. CLI печатает ровно четыре строки. Их собирает `build_contract` в `main.py`: `Status` и `Data` берутся из фактического ответа tool, а не из пересказа модели.

```text
Status: success | error
Action: <что сделано или почему отказано>
Data: <JSON тела ответа API или {}>
Errors: <текст ошибки или none>
```

`Status: success` ставится только после HTTP 200 или 201 от User Directory. Запрос вне каталога пользователей, нехватка обязательного поля и ответ API 4xx/5xx — это `Status: error`. Tool при нехватке данных и при чужой теме не вызывается.

Пользовательский запрос оборачивается шаблоном из `prompts/user_template.md`.

## Tool

Один tool `user_directory_api` в `tools/api_tool.py` покрывает все четыре операции параметром `operation`. Внутри — HTTP-вызов через `httpx`. Результат tool печатается в консоль строкой `[tool:user_directory_api]`.
