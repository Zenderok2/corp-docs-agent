# Запуск на Windows

Для нативного запуска нужны Python 3.12, Node.js от 22.12, сборка
llama.cpp для Windows и веса Qwen в формате GGUF. Команды ниже выполняются
из корня репозитория. Для запуска в контейнерах есть
[инструкция Docker Compose](../../infra/docker/README.md).

## 1. Установить зависимости

```powershell
py -3.12 -m venv runtime/dev-venv
.\runtime\dev-venv\Scripts\python.exe -m pip install -r backend/requirements.lock -e './backend[importers,test]'
npm.cmd --prefix frontend ci
```

Для локальных настроек создайте `.env` по образцу
[.env.example](../../.env.example). Для входа службы качества задайте
`QUALITY_ACCESS_CODE`. При импорте рабочего корпуса задайте отдельный
`STAFF_ACCESS_CODE`.

## 2. Запустить модель

Распакуйте CPU-сборку llama.cpp b12535 для Windows x64 в
`runtime/local-llm/b12535`. Скрипт `infra/docker/fetch-models.sh`
загружает веса в `runtime/models` и сверяет SHA-256. На Windows его
можно запустить из Git Bash. [Сборка llama.cpp](https://github.com/ggml-org/llama.cpp/releases/tag/b12535) ·
[Скрипт загрузки](../../infra/docker/fetch-models.sh).

```bash
bash infra/docker/fetch-models.sh
```

В отдельном окне PowerShell запустите модель. Адрес и имя совпадают
с `LLM_BASE_URL` и `LLM_MODEL` из образца настроек.

```powershell
.\runtime\local-llm\b12535\llama-server.exe --model runtime/models/chat.gguf --host 127.0.0.1 --port 8081 --alias corp-chat --ctx-size 6144 --threads 4 --parallel 1 --no-webui --log-disable
```

## 3. Импортировать примеры и запустить приложение

В терминале backend импортируйте вымышленный корпус и запустите API.
`APP_STORAGE_ROOT` указывает на папку SQLite и исходников.

```powershell
$env:APP_STORAGE_ROOT = 'runtime/dev-data'
.\runtime\dev-venv\Scripts\corp-docs.exe import data/samples/catalog.json
.\runtime\dev-venv\Scripts\python.exe -m uvicorn corp_docs_agent.api.main:app --host 127.0.0.1 --port 8000 --no-access-log
```

В третьем терминале запустите интерфейс.

```powershell
npm.cmd --prefix frontend run dev
```

Откройте `http://127.0.0.1:5173`. Доступ к вымышленным документам
открывается сразу. Можно выбрать готовый вопрос и открыть цитату в ответе.

## 4. Проверить запуск

```powershell
Invoke-RestMethod http://127.0.0.1:8081/health
Invoke-RestMethod http://127.0.0.1:8000/health/ready
.\runtime\dev-venv\Scripts\python.exe -m pytest -q backend/tests
.\runtime\dev-venv\Scripts\python.exe evals/smoke.py --base-url http://127.0.0.1:8000
```

Импорт рабочего набора описан в [backend/README](../../backend/README.md).
Для гибридного поиска используйте
[семантический профиль Compose](../../infra/docker/README.md#запуск).
