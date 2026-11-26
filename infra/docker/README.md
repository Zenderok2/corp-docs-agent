# Docker Compose и стенд

Команды выполняются из корня проекта. В Compose только Caddy публикует
порты на хосте. API, SQLite, Chroma и оба рантайма моделей работают в
приватной сети. SQLite и сертификаты сохраняются в именованных томах.

## Запуск

```bash
bash infra/docker/fetch-models.sh
docker compose build api web
docker compose run --rm --no-deps operator
docker compose up -d
curl -fsS http://localhost:8080/health/ready
```

`operator` читает `data` для CLI-импорта. Для материалов заказчика передайте
`corp-docs import /corpus/inbox/catalog.json`. Демо- и рабочий корпус имеют
разные разрешения, примеры отмечены группой `demo`.

Для Chroma запустите `docker compose --profile semantic up -d`, дождитесь
готовности embeddings и выполните
`docker compose run --rm --no-deps operator corp-docs rebuild-vectors`.
Укажите `RETRIEVAL_MODE=hybrid` и запустите
`docker compose --profile semantic up -d api`.

## Публикация на Linux

Зафиксируйте `BUILD_SHA` и пересоберите `api` и `web`. В закрытом `.env`
укажите `APP_ENV=hosted`, `SITE_ADDRESS` для Caddy и HTTPS-origin
`PUBLIC_ORIGIN`. Пробросьте `HTTP_BIND=0.0.0.0:80` и
`HTTPS_BIND=0.0.0.0:443`. Используйте отдельные случайные значения для
`QUALITY_ACCESS_CODE`, `SESSION_SECRET` и, при рабочем корпусе,
`STAFF_ACCESS_CODE`.

Образы остаются на ВМ. Политика `restart: unless-stopped` восстанавливает
сервисы после перезапуска Docker или хоста. Убедитесь, что публичный URL
отдаёт страницу и `/health/ready`, а у сборки верный SHA. Проверьте повторный
вход после перезапуска с тем же SQLite-томом и секретом сессии.

Скрипт `bootstrap-yandex-vm.sh` устанавливает Docker из репозитория Ubuntu
на выделенной ВМ и принимает дату окончания доступа по МСК. Таймер systemd
завершит работу ОС после периода проверки, сохранив диск для восстановления.
По положению показ жюри нужен до 3 декабря, 23:59 МСК. Использование диска
тарифицируется отдельно от вычислительных ресурсов.

## Профили

| Компонент | Версия |
| --- | --- |
| Python | 3.12.12 slim Bookworm |
| Node.js | 24.16.0 Bookworm |
| Caddy | 2.11.2 Alpine |
| llama.cpp | b12535 |
| ChromaDB | 1.3.5 |
| Chat | Qwen3-4B-Instruct-2507 Q4_K_M |
| Embeddings | Qwen3-Embedding-0.6B Q8_0, 1 024 числа |

Поставщики весов и контрольные суммы зафиксированы в
[скрипте загрузки](fetch-models.sh). Веса Qwen и конверсия для chat
распространяются под Apache-2.0. llama.cpp использует MIT. PyMuPDF имеет
двойную модель лицензирования AGPL и коммерческой лицензии и включён в
CLI-импорт. [PyMuPDF на PyPI](https://pypi.org/project/PyMuPDF/1.26.6/) ·
[Модель Qwen](https://huggingface.co/Qwen/Qwen3-4B-Instruct-2507).
