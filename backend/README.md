# Backend и локальный запуск

Нужны Python 3.12, Node.js и локальные веса. Подготовленную ранее `.venv`
хранят отдельно. Она относится к предыдущему кейсу.

## Запуск на Windows

В корне проекта создайте отдельную среду и импортируйте вымышленные документы.

```powershell
py -3.12 -m venv runtime/agent-venv
.\runtime\agent-venv\Scripts\python.exe -m pip install -r backend/requirements.lock -e './backend[test]'
$env:APP_STORAGE_ROOT = 'runtime/demo'
.\runtime\agent-venv\Scripts\corp-docs.exe import data/samples/catalog.json
```

Запустите локальный llama.cpp chat на `127.0.0.1:8081` с alias `corp-chat`.
Для проверки совместимости в шаблоне доступны веса Qwen2.5 0.5B. Целевой
профиль стенда — Qwen3 4B Instruct 2507. Для нескольких объёмных фрагментов
выделите не менее 6 144 токенов контекста. [Команды и подготовка моделей](../docs/operations/LOCAL_SETUP.md).

В терминале backend запустите API. Чтобы открыть тестовую почту службы качества, задайте
свой `QUALITY_ACCESS_CODE` в `.env` перед запуском.

```powershell
$env:APP_STORAGE_ROOT = 'runtime/demo'
.\runtime\agent-venv\Scripts\python.exe -m uvicorn corp_docs_agent.api.main:app --host 127.0.0.1 --port 8000 --no-access-log
```

В отдельном терминале из корня выполните `npm --prefix frontend ci` и
`npm --prefix frontend run dev`. Откройте `http://127.0.0.1:5173`.
Статус зависимостей возвращает `http://127.0.0.1:8000/health/ready`.

## Импорт рабочего корпуса

`corp-docs import` принимает JSON-каталог. Пути внутри каталога разрешаются
относительно его папки. Для каждого файла укажите `path`, `key`, `version`,
`title` и `kind`. Значения `kind` — `regulations` и `product_licensing`.
Для рабочего корпуса выберите группу `staff` и задайте отдельный код участника.
[Пример каталога](../data/samples/catalog.json).

- По умолчанию статус редакции `unknown`. Укажите `published` и подтверждённые
  даты, когда основание найдено в материалах. Если подтверждено только текущее
  действие редакции, укажите `publication_confirmed_on` и оставьте `effective_from`
  пустым. Для более раннего вопроса агент пометит действие редакции как неизвестное.
  Если у экземпляра отсутствует номер редакции, можно задать технический `version`
  и понятный пользователю `revision_label`.
- В `scope` можно указать юрлицо, подразделение, продукт и тип лицензии.
  `supersedes` и `related_to` принимают ссылки вида `key@version`.
- При изменении текста создайте новую редакцию. Для текущей редакции можно
  уточнить метаданные с сохранением SHA-256, координат и ссылок.
- Для ранее импортированных DOCX повторный импорт добавляет номера пунктов и
  заголовки столбцов из документа с сохранением прежних ссылок на цитаты.
- Импорт отчитается по каждому файлу. Пустые страницы отображаются как
  предупреждения, полностью сканированный PDF получает код `needs_ocr`.

Гибридный режим включает локальный embeddings-сервер и Chroma. После импорта
запустите `corp-docs rebuild-vectors`, затем укажите `RETRIEVAL_MODE=hybrid`.
При устаревшей генерации поиск продолжает работать по FTS5, а readiness
показывает, что векторная часть требует пересборки.

## Проверки

```powershell
.\runtime\agent-venv\Scripts\python.exe -m pytest -q backend/tests
.\runtime\agent-venv\Scripts\python.exe evals/smoke.py --base-url http://127.0.0.1:8000
```

[Контракт API](../docs/architecture/API.md) · [Контрольные сценарии](../evals/README.md).
