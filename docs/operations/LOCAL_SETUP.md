# Локальная среда

Проверено 19 ноября 2026. Здесь описан подготовленный runtime для моделей.
Backend и frontend прототипа создаются в спринте.

Для переноса подготовленной среды на другой компьютер используйте
[инструкцию переноса](TRANSFER.md).

## Проверено фактически

| Инструмент | Результат |
| --- | --- |
| Python в корневой .venv | 3.12.2; окружение осталось от прежнего кейса |
| SQLite в этом Python | 3.43.1; создание FTS5 таблицы в памяти успешно |
| Node | 22.14.0 |
| npm | 11.1.0 |
| Docker CLI | 29.4.2 |
| Docker Compose CLI | v5.1.3 |
| Docker Engine | Недоступен по dockerDesktopLinuxEngine при проверке |
| Машина | Intel Core i5-12450H, 7,7 ГБ RAM, Intel UHD. Свободно около 0,9–1,5 ГБ RAM в момент проверки |
| llama.cpp | Windows CPU x64, b12535, установлен локально в runtime/local-llm |
| Chat | Qwen2.5-0.5B-Instruct Q4_K_M, 2 048 токенов контекста. Один запрос с двумя вымышленными источниками отработал за 1,06 с |
| Embeddings | Qwen3-Embedding-0.6B Q8_0, pooling last. Получены 3 конечных вектора по 1 024 числа за 0,50 с, релевантный пример оказался ближе постороннего |
| Оба сервера вместе | Два коротких запроса прошли за 0,88 с при одновременно запущенных процессах. Длительная нагрузка и память полного приложения ещё не измерены |

Наличие Docker CLI не подтверждает работающий daemon. Docker Desktop не запускался,
образы не скачивались и контейнеры не создавались. Сборка React и зависимости
нового Python-проекта не проверялись.

Старая `.venv` сохранена для подготовительных инструментов. Её snapshot находится
в [архиве](../../archive/support-agent/docs/preparation/environment-windows-py312.txt)
и относится к Gemini/Streamlit, а не к новому стеку. Не устанавливать этот snapshot
как зависимости нового приложения. Глобальный Python не менялся.

## Запуск локальных моделей

Файлы загружены с официальных страниц. SHA-256 архива Windows и GGUF совпали
с данными издателей. Бинарники и веса находятся в игнорируемом `runtime/local-llm/`.
Источники: [сборка b12535](https://github.com/ggml-org/llama.cpp/releases/tag/b12535),
[Qwen chat](https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct-GGUF),
[Qwen embeddings](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B-GGUF).

Команды выполняются из корня проекта. В отдельном терминале запустить chat:

```powershell
.\runtime\local-llm\b12535\llama-server.exe -m .\runtime\local-llm\models\qwen2.5-0.5b-instruct-q4_k_m.gguf --host 127.0.0.1 --port 8081 --alias corp-chat -ngl 0 -c 2048 -np 1 -t 4
```

Для режима hybrid запустить второй процесс:

```powershell
.\runtime\local-llm\b12535\llama-server.exe -m .\runtime\local-llm\models\Qwen3-Embedding-0.6B-Q8_0.gguf --host 127.0.0.1 --port 8082 --alias corp-embed --embedding --pooling last -ngl 0 -c 1024 -b 512 -ub 512 -np 1 -t 4
```

В другом терминале проверить готовность после загрузки весов:

```powershell
Invoke-RestMethod http://127.0.0.1:8081/health
Invoke-RestMethod http://127.0.0.1:8081/v1/models
# Для запущенного embeddings-процесса
Invoke-RestMethod http://127.0.0.1:8082/health
```

Chat проверен через `POST /v1/chat/completions`, embeddings через
`POST /v1/embeddings`. В smoke использован только вымышленный текст.
Модель 0.5B подходит для проверки входов и выходов. Сравнить её с
[Qwen2.5-1.5B-Instruct Q4](https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF)
или более сильной локальной моделью на контрольном корпусе. Профиль 1.5B
здесь ещё не проверен. Для конкурсной версии зафиксировать модель, квантование,
SHA весов, сборку runtime и результат проверки.
Завершать процессы Ctrl+C в их терминалах. Тестовые процессы остановлены.

## Доступные команды диагностики

```powershell
Set-Location -LiteralPath 'C:\Users\Rodion\.gemini\antigravity\scratch\ai-support-agent'
.\.venv\Scripts\python.exe --version
node --version
npm --version
docker --version
docker compose version
docker info
git status --short
```

`docker info` пока ожидаемо показывает недоступный Engine. Для будущей сборки
сначала потребуется работающий Linux-container engine; это не мешает текущему проектированию.

## Порядок создания приложения в спринте

1. Сверить [этап](../preparation/HACKATHON.md), бриф и разрешённый корпус.
2. Создать backend pyproject с runtime/dev extras из
   [плана зависимостей](../architecture/STACK.md). Для чистой сборки использовать
   изолированную среду, а не считать накопленные пакеты старой `.venv` достаточными.
3. Создать Vite react-ts проект в существующем frontend, сохранив README и
   каталоги feature. Не перезаписывать каталог генератором без проверки файлов.
4. Проверить версии пакетов, typecheck и build; сохранить package-lock.json.
5. Добавить settings, миграции SQLite, runner и API; сформировать backend lock.
6. Выполнить полный путь на выданном корпусе и собственных контрольных вопросах
   в нативном режиме FTS5 + llama.cpp. Затем измерить hybrid с Chroma.
7. Упаковать проверенный запуск в Compose при готовом Engine по
   [спецификации](../../infra/docker/README.md).

Исполняемого compose.yaml и package.json пока нет: команды `docker compose up`,
`npm ci` и `uvicorn corp_docs_agent...` до этих шагов неприменимы.
Локальный `origin` указывает на [репозиторий команды](https://github.com/Zenderok2/corp-docs-agent).
На 25 ноября в репозитории нет коммитов. До начала спринта можно пользоваться
локальными инструкциями и [готовить перенос](TRANSFER.md).

## Настройки

Корневая [.env.example](../../.env.example) задаёт будущие настройки приложения
для нативного запуска с уже проверенными localhost-адресами и alias.

- LLM_* — генерация, EMBEDDING_* — векторизация, отдельно document/query models.
- RETRIEVAL_MODE=lexical на первом проходе. В hybrid включаются embeddings и Chroma.
- EMBEDDING_PROFILE_VERSION и размерность входят в идентичность индекса.
- CHROMA_HOST=127.0.0.1, port=8001 — будущая разработка с опубликованным портом;
  в Compose использовать chroma:8000.
- runtime — SQLite/оригиналы/артефакты, исключён из Git.
- VITE_API_BASE_URL — только публичный URL. Backend/Compose читают локальный .env
  явно; браузерной сборке передаётся только необходимая публичная настройка.
  Ключи никогда не имеют префикс VITE_ и не включаются в frontend image.

Веса скачиваются заранее. Во время запуска приложения модели читаются с диска.
Для финальной проверки запустить весь прототип на выбранном хосте после
отключения исходящего доступа, сохранив локальное соединение между сервисами.

## Будущие проверки сборки

Backend: lint + значимые unit/integration/contract tests без внешних ключей.
Frontend: typecheck + production build + сценарий question/citation/upload.
Контейнеры: ready → import → index → ask → restart → ask, с сохранёнными
томами. Качество поиска и ответов проверять отдельно от smoke модельных endpoint'ов.

Chroma, полный путь с документами задачи и сборка контейнеров пока ожидают спринта.
