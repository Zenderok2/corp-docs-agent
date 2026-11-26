# Backend: FastAPI

Структура подготовлена; Python-модули нового приложения пока не реализованы.
Имя будущего пакета: `corp_docs_agent`, src-layout.

| Каталог | Ответственность | Не должен делать |
| --- | --- | --- |
| src/corp_docs_agent/api | Documents, ingestion jobs, questions, answers, sources, feedback; схемы и principal | Прямые вызовы модели или SQL из роутов |
| src/corp_docs_agent/core | Settings, DI, lifecycle, тайм-ауты и безопасные ошибки | Хранить ключи в коде |
| src/corp_docs_agent/domain | Document, Version, Block, Chunk, Citation, Answer; правила актуальности | Импортировать FastAPI, SDK Chroma или LLM |
| src/corp_docs_agent/ingestion | Проверка файлов, PDF/DOCX parsers, chunking, индексирующие задания | Делать загруженный файл автоматически действующим |
| src/corp_docs_agent/retrieval | Lexical/vector retrieval, фильтры, RRF, контекст | Возвращать неразрешённые/неопубликованные фрагменты |
| src/corp_docs_agent/agent | Ограниченный поиск → уточнение → проверка доказательств → ответ | Менять документы, права или внешние системы |
| src/corp_docs_agent/providers | ChatProvider, EmbeddingProvider, VectorIndex и адаптеры локального llama.cpp | Протаскивать SDK-объекты в домен и HTTP-контракты |
| src/corp_docs_agent/persistence | SQLite, миграции, репозитории, FTS5 и задания | Считать Chroma источником истины для доступа |
| tests | Unit/integration/contract проверки | Вызывать платные API по умолчанию |

Первый сценарий проходит от файлового импорта и FTS5 до ответа с проверенными
цитатами по двум документам. Затем добавить React, повторный поиск, локальные
embeddings и Chroma. Административный интерфейс зависит от полного ТЗ.

В спринте добавить `pyproject.toml`, lock, entrypoint, миграции и тесты.
Ожидаемый сервер: один backend с ограниченным runner заданий; CPU-разбор
файлов вынести из event loop. Начальная модель развёртывания — один worker.
Стратегия восстановления описана в [архитектуре](../docs/architecture/DESIGN.md).
