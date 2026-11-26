# Перенос проекта на другой Windows‑ПК

Инструкция рассчитана на Windows x64 и PowerShell. В текущей папке есть план
разработки, заготовка проекта и проверенные локальные модели. После переноса
можно сразу проверить модели. Для демонстрации агента понадобится сборка,
которую предстоит реализовать в спринте.

## 1. Подготовить компьютер для демонстрации

1. Выбрать локальную папку, например `C:\Projects\corp-docs-agent`.
2. Установить Git, Python 3.12 и Node.js 22. На исходном ПК проверены
   Python 3.12.2 и Node 22.14.0. Для проверки моделей достаточно Windows x64
   и файлов llama.cpp из комплекта.
3. Для сборки через Compose понадобится работающий Docker Engine. На первом
   проходе можно запускать модели и приложение нативно. Подробнее о режимах
   в [инструкции по локальной среде](LOCAL_SETUP.md).
4. Проверить свободное место под модельный комплект, зависимости, корпус
   и запись видео. Текущие модели и CPU-бинарники занимают около 1,15 ГиБ.

В PowerShell проверить, что установленные программы доступны:

```powershell
git --version
py -3.12 --version
node --version
npm.cmd --version
# Если планируется Compose, выполнить после запуска Docker Desktop
docker compose version
docker info --format '{{.ServerVersion}}'
```

## 2. Скопировать шаблон и модели

Текущая версия на 25 ноября лежит в рабочей папке без коммитов. На GitHub
уже создан пустой [репозиторий команды](https://github.com/Zenderok2/corp-docs-agent).
Переносить подготовку нужно копированием папки, включая `.git` с настроенным
`origin`. После публикации коммитов можно клонировать репозиторий и отдельно
перенести модели и документы. Они исключены из Git.

При ручном копировании через Проводник перенести корневые файлы
`README.md`, `AGENTS.md`, `.env.example`, `.editorconfig`, `.gitignore`,
каталоги `.git`, `backend`, `frontend`, `docs`, `infra`, `data`, `evals`
и `archive`. Скрытые файлы должны быть видны. Из `runtime` достаточно полной
папки `local-llm/b12535` со всеми DLL и папки `local-llm/models` с весами.
На целевой машине окружение Python и зависимости Node создаются заново.
Пропустить `.venv`, `node_modules`, кэши, рабочие базы, секреты `.env`
и содержимое `data/inbox/`. Шаблон `.env.example` и файл `data/inbox/.gitkeep`
сохранить. Документы при необходимости скопировать отдельно по шагу ниже.

Вместо ручного копирования можно выполнить следующий блок на исходном ПК.
Открыть PowerShell **в корне проекта**, заменить `E:\corp-docs-agent` на новую
папку на флешке или диске. Скрипт рассчитан на новую папку назначения.

```powershell
$projectSource = (Get-Location).Path
$projectCopy = [IO.Path]::GetFullPath('E:\corp-docs-agent')
if (-not (Test-Path -LiteralPath (Join-Path $projectSource 'docs\preparation\KICKOFF.md'))) {
    throw 'Откройте PowerShell в корне исходного проекта.'
}
if ((Test-Path -LiteralPath $projectCopy) -or
    $projectCopy.StartsWith($projectSource.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Укажите новую папку за пределами исходного проекта.'
}

$skipDirs = @(
    '.venv', 'venv', 'node_modules', '__pycache__', '.ruff_cache', '.pytest_cache',
    (Join-Path $projectSource 'runtime'),
    (Join-Path $projectSource 'data\inbox'),
    (Join-Path $projectSource 'data\private')
)
robocopy.exe $projectSource $projectCopy /E /XJ /R:2 /W:1 /XD $skipDirs /XF .env '.env.*' '*.gguf' '*.log' '*.sqlite*' '*.db' '*.db-*'
if ($LASTEXITCODE -ge 8) { throw 'Не удалось скопировать проект.' }

foreach ($template in '.env.example', 'archive\support-agent\.env.example', 'data\inbox\.gitkeep') {
    $original = Join-Path $projectSource $template
    if (Test-Path -LiteralPath $original -PathType Leaf) {
        $target = Join-Path $projectCopy $template
        New-Item -ItemType Directory -Path (Split-Path -Parent $target) -Force | Out-Null
        Copy-Item -LiteralPath $original -Destination $target -ErrorAction Stop
    }
}

$llmSource = Join-Path $projectSource 'runtime\local-llm'
$llmCopy = Join-Path $projectCopy 'runtime\local-llm'
robocopy.exe (Join-Path $llmSource 'b12535') (Join-Path $llmCopy 'b12535') /E /XJ /R:2 /W:1
if ($LASTEXITCODE -ge 8) { throw 'Не удалось скопировать llama.cpp.' }

robocopy.exe (Join-Path $llmSource 'models') (Join-Path $llmCopy 'models') qwen2.5-0.5b-instruct-q4_k_m.gguf Qwen3-Embedding-0.6B-Q8_0.gguf /R:2 /W:1
if ($LASTEXITCODE -ge 8) { throw 'Не удалось скопировать веса моделей.' }

foreach ($file in 'b12535\llama-server.exe', 'models\qwen2.5-0.5b-instruct-q4_k_m.gguf', 'models\Qwen3-Embedding-0.6B-Q8_0.gguf') {
    if (-not (Test-Path -LiteralPath (Join-Path $llmCopy $file) -PathType Leaf)) {
        throw "В копии отсутствует $file"
    }
}
```

Коды Robocopy от 0 до 7 означают, что операция завершилась без ошибки
копирования. После переноса с флешки скопировать получившуюся папку на
локальный диск целевого ПК и работать оттуда.

Когда выдадут документы задачи, переносить их отдельно напрямую на компьютер
команды. Положить регламенты в `data/inbox/regulations/`, материалы о продуктах
и лицензировании в `data/inbox/product_licensing/`. Создать папки при необходимости.
После переезда предпочтительно импортировать корпус заново, чтобы индекс
соответствовал файлам и выбранному профилю embeddings.

## 3. Проверить перенос и запустить модели

На новом ПК открыть PowerShell **в скопированной папке** и сверить веса:

```powershell
$modelFiles = @{
    'qwen2.5-0.5b-instruct-q4_k_m.gguf' = '74a4da8c9fdbcd15bd1f6d01d621410d31c6fc00986f5eb687824e7b93d7a9db'
    'Qwen3-Embedding-0.6B-Q8_0.gguf' = '06507c7b42688469c4e7298b0a1e16deff06caf291cf0a5b278c308249c3e439'
}
foreach ($name in $modelFiles.Keys) {
    $modelPath = Join-Path '.\runtime\local-llm\models' $name
    $hash = (Get-FileHash -LiteralPath $modelPath -Algorithm SHA256 -ErrorAction Stop).Hash
    if ($hash -ine $modelFiles[$name]) { throw "Контрольная сумма не совпала: $name" }
    Write-Output "Проверено $name"
}
.\runtime\local-llm\b12535\llama-server.exe --version
git status --short --branch
```

В отдельных терминалах из корня проекта запустить chat и, если нужен
hybrid-режим, embeddings. Использовать [команды для модели и embeddings](LOCAL_SETUP.md#запуск-локальных-моделей).
Проверенный CPU-профиль работает без CUDA. Если терминал сообщает, что порт
занят, выбрать свободный порт и указать его также в локальном `.env`.

Проверка генерации в третьем терминале:

```powershell
$chatBody = @{
    model = 'corp-chat'
    messages = @(@{ role = 'user'; content = 'Тестовый регламент: заявку проверяет финансовый отдел. Кто проверяет заявку?' })
    temperature = 0
    max_tokens = 48
} | ConvertTo-Json -Depth 4

$chat = Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8081/v1/chat/completions' `
    -ContentType 'application/json; charset=utf-8' -Body $chatBody -TimeoutSec 45
$chat.choices[0].message.content
```

В ответе должен быть назван финансовый отдел. Если запущен embeddings-сервер,
проверить пинг и получить вектор из 1024 чисел:

```powershell
Invoke-RestMethod -Uri 'http://127.0.0.1:8082/health'
$embedBody = @{ model = 'corp-embed'; input = @('Тестовый документ') } | ConvertTo-Json
$embed = Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8082/v1/embeddings' `
    -ContentType 'application/json; charset=utf-8' -Body $embedBody -TimeoutSec 45
$embed.data[0].embedding.Count
```

Эти запросы проверяют модели. Готовность агента проверяется отдельно после
реализации, включая ответы на вопросы по нескольким документам.

## 4. Подготовить конкурсную сборку к показу

1. Когда появится код, собрать на целевом ПК **ту же версию, которую будете сдавать**.
   Использовать инструкции и зафиксированные зависимости этой версии. Для
   нативного режима создать `.venv` через `py -3.12 -m venv .venv` и установить
   зависимости по lock-файлу проекта. Для frontend использовать `npm.cmd ci`
   из `frontend/`, когда появится `package-lock.json`. Если выбран Compose,
   заранее собрать и проверить образы по инструкции готовой версии.
2. Создать локальный `.env` из `.env.example`, если его ещё нет. Проверить
   адреса моделей и режим поиска. При замене chat-модели повторить контрольные
   вопросы. При замене embeddings создать индекс заново и повторить поиск.
3. Перезапустить все сервисы и импортировать материалы. Открыть интерфейс,
   проверить составной вопрос с двумя цитатами, вопрос по лицензированию
   и ситуацию, когда ответ нельзя подтвердить. Проверить, что ссылки
   открывают именно нужные фрагменты. Список в [плане приёмки](../quality/EVALUATION.md).
4. Измерить время полного ответа на новом ПК при открытом браузере и записи экрана.
   Сначала проверить CPU-профиль как резерв, потом подобрать более сильную
   локальную модель по качеству и задержке.
5. Для прогона приложения без внешних сервисов заранее подготовить зависимости,
   веса и индекс. Проверить локальное демо при отключённом внешнем доступе.
   Если показ идёт по видеосвязи, оставить связь и убедиться, что само приложение
   продолжает использовать только свой контур.
6. Записать резервный показ на этом ПК. Проверка на ПК готовит локальный контур,
   а для сдачи нужна рабочая версия по доступному жюри URL. Сверить версию
   стенда, видео, калькулятор и репозиторий по
   [чек-листу сдачи](../preparation/KICKOFF.md#перед-сдачей).

Для команды, которая переносит проект до спринта, следующий материал —
[план первого часа](../preparation/KICKOFF.md). Он связан с
[архитектурой](../architecture/DESIGN.md) и [планом на 48 часов](../SPRINT.md).
