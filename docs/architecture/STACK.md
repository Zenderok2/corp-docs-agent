# Стек и зависимости

Обновлено 19 ноября 2026. Версии зависимостей приложения фиксируются
во время реализации. Подготовка моделей описана в [локальной среде](../operations/LOCAL_SETUP.md).

| Выбор | Решение |
| --- | --- |
| React + Vite | Сохранить, добавить TypeScript. После появления OpenAPI типизировать клиент и состояния ответа |
| FastAPI | Сохранить. Pydantic-схемы, сервисный слой, отдельная обработка долгих задач |
| SQLite | Сохранить для небольшого прототипа; добавить FTS5 и миграции |
| ChromaDB | Подключить после базового прохода на FTS5, отдельным сервисом при упаковке |
| PyMuPDF | Сохранить за интерфейсом parser; проверить извлечение/таблицы на реальных PDF |
| python-docx | Сохранить; адресация по блокам, а не придуманным страницам |
| Локальная генерация | llama.cpp, HTTP API в контуре команды. На CPU начать с Qwen2.5-0.5B Q4 для проверки запуска |
| Локальные embeddings | Qwen3-Embedding-0.6B Q8 в отдельном процессе llama.cpp |
| Docker Compose | Этап упаковки. Для текущего Windows первый запуск нативный, без зависимости от Docker Engine |

## Минимальные группы зависимостей для спринта

Backend runtime: fastapi, uvicorn, pydantic-settings, httpx, python-multipart,
SQLAlchemy + Alembic для SQLite, chromadb client, PyMuPDF, python-docx.
Для Chroma HTTP-only пакет/режим выбрать по проверенной версии и доступным API;
не ставить одновременно конфликтующие варианты клиента.

Backend dev: pytest, pytest-asyncio при async-проверках, Ruff.
Frontend: react, react-dom, typescript, vite, React-плагин Vite;
далее по необходимости query/router/test libraries. Начать с простого состояния
и polling, без тяжёлого UI framework и отдельной машины состояний.

Не добавлять LangChain/LlamaIndex только ради обёрток: текущая структура
позволяет небольшой явный pipeline. Если корпус/сроки оправдают библиотеку,
она подключается внутри модуля, не меняя все доменные контракты.

## Chat и embeddings

У генерации и embeddings отдельные локальные URL, модель, тайм-ауты и бюджеты.
EmbeddingProvider разделяет `embed_documents` и `embed_query`. Для Qwen3
сохранять шаблон инструкции для query, pooling и проверенную размерность в профиле
индекса. Источники: [llama.cpp server](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)
и [Qwen3 Embedding](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B).

Перед реализацией адаптера проверить локальные endpoint, model alias,
batch/размер входа, usage, error format и возможность ограничить
ответ схемой. Не предполагать, что любой совместимый API поддерживает
Responses, tool calling, JSON schema или streaming одинаково. Базовый RAG
может работать через chat completions + валидируемый JSON без нативных tool calls;
управляемый цикл выбора инструмента остаётся на стороне приложения.

Qwen2.5-0.5B — профиль для проверки запуска на имеющемся ПК. Для финальной
генерации сравнить с более сильным локальным кандидатом на примерах из двух
наборов. Если RAM ограничивает одновременную работу chat и embeddings,
сначала измерить режим FTS5 с chat. Для гибридного поиска обе модели нужны
при ответе, поэтому разовый офлайн-импорт векторов сам по себе не решает вопрос RAM.

При недоступности embeddings не переключаться молча на другую модель с тем
же числом измерений. Явный lexical-only режим допустим только с маркировкой
ограничения; иначе сервис сообщает о недоступности выбранного поиска.

## Chroma

Backend явно вычисляет и передаёт embeddings. Нельзя случайно включить
default embedding function Chroma и получить другой профиль/скрытую загрузку
модели. Сверить API выбранной версии клиента и сервера, фильтры и persistence.
Источники: [Python client](https://docs.trychroma.com/reference/python),
[документация Chroma](https://docs.trychroma.com/).

Одна collection/generation не смешивает разные профили. В metadata —
document_version_id, chunk_id, scope и generation_id; они не заменяют
авторитетные проверки SQLite. В браузере Chroma SDK не используется.

## PDF/DOCX

PyMuPDF лицензируется по AGPL или коммерческому соглашению. Перед выбором
модели поставки учесть это условие; закрытый пилот и лицензирование пока
не определены. Альтернативный parser можно подключить через тот же интерфейс.
Источник: [лицензирование PyMuPDF](https://pymupdf.readthedocs.io/en/latest/about.html).

python-docx даёт доступ к абзацам/таблицам; для ссылок сохранять порядок блоков
и locator исходной версии. Встроенные страницы DOCX не заменяют отдельный
rendering pipeline. Источник: [Document objects](https://python-docx.readthedocs.io/en/latest/api/document.html).

## Фиксация среды

В спринте: backend pyproject + проверенный lock; frontend package-lock.json;
Docker images с версиями/при необходимости digest. Не использовать непроверенные
диапазоны `>=` как гарантию воспроизводимости.

Установленный Node 22.14.0 удовлетворяет опубликованному требованию Vite
22.12+, но конкретный шаблон и версия пакетов ещё не проверены.
Источник: [Vite getting started](https://vite.dev/guide/).
