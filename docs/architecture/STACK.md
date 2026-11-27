# Стек и зависимости

| Часть | Что используется |
| --- | --- |
| Интерфейс | React 19, TypeScript и Vite. Vite в режиме разработки проксирует API, а в сборке файлы отдаёт Caddy |
| Backend | Python 3.12, FastAPI, Pydantic, Uvicorn и HTTPX |
| Данные и очередь | SQLite. В базе хранятся редакции документов, права, фрагменты, задания, история и консультации |
| Импорт | PyMuPDF для PDF, python-docx для DOCX, встроенный разбор UTF-8 для TXT и Markdown |
| Поиск по умолчанию | FTS5 с морфологией и словарём сокращений |
| Гибридный профиль | Qwen3-Embedding-0.6B Q8_0 и ChromaDB 1.3.5 |
| Ответы | Qwen3-4B-Instruct-2507 Q4_K_M в llama.cpp |
| Развёртывание | Docker Compose для воспроизведения. Публичный стенд работает на ВМ Yandex Cloud |

Chat и embeddings запускаются разными локальными HTTP-процессами. Backend
считает векторы сам и отправляет их в Chroma. Профиль индекса включает
модель и размерность. Права и применимость документов проверяются по SQLite.

Файлы с версиями и контрольными суммами

- [Зависимости Python](../../backend/requirements.lock)
- [Зависимости frontend](../../frontend/package-lock.json)
- [Профили контейнеров](../../compose.yaml)
- [Веса моделей](../../infra/docker/fetch-models.sh)

PyMuPDF распространяется по AGPL или коммерческой лицензии Artifex.
[Условия PyMuPDF](https://pymupdf.readthedocs.io/en/latest/about.html#license-and-copyright).

[Запуск на Windows](../operations/LOCAL_SETUP.md) ·
[Docker Compose](../../infra/docker/README.md) · [Отчёт о замерах](../quality/RELEASE_2026_11_27.md).
