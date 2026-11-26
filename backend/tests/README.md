# Будущие проверки backend

`unit/`: правила редакций, locator/citation, chunking, схемы, scope и бюджеты.
`integration/`: API + временная SQLite + тестовый векторный индекс + scripted
провайдеры; отдельный контрактный прогон с реальной Chroma в тестовом контуре.

Сценарии: [EVALUATION.md](../../docs/quality/EVALUATION.md).
Автотестовых файлов пока нет. Прогоны с локальной моделью отмечать отдельно
от проверок со scripted embeddings и оценивать на размеченных вопросах.
