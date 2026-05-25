# Установка Qdrant без Docker

Инструкция для macOS и Windows — устанавливаем Qdrant нативно, с автозапуском и переносом существующих коллекций.

---

## macOS

### Вариант 1 — Homebrew (рекомендуется)

```bash
# Установка
brew install qdrant/tap/qdrant

# Настройка пути к данным (если коллекции уже есть на диске)
echo 'storage:
  storage_path: /Users/$(whoami)/qdrant_storage' > /opt/homebrew/etc/qdrant/config.yaml

# Запуск и автостарт
brew services start qdrant
```

После этого Qdrant слушает `localhost:6333` и автоматически запускается при каждом входе в систему.

Проверка:

```bash
curl -s http://localhost:6333/health | jq
```

Ожидаемый ответ: `{"status":"ok","title":"qdrant","version":"..."}`

### Вариант 2 — бинарник вручную

```bash
# Скачать последний релиз под aarch64 (Apple Silicon) или x86_64 (Intel)
# https://github.com/qdrant/qdrant/releases/latest

# Распаковать и запустить с явным путём к данным
./qdrant --storage-path /Users/$(whoami)/qdrant_storage
```

Для автостарта — добавить через macOS System Settings → General → Login Items.

---

## Windows

### Вариант 1 — Scoop + NSSM (рекомендуется)

Открой PowerShell (Admin) и выполни:

```powershell
# Установка scoop (если ещё нет)
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
irm get.scoop.sh | iex

# Установка qdrant и nssm
scoop install qdrant nssm

# Создание папки для данных
mkdir C:\Users\whoami\qdrant_storage -Force

# Регистрация как Windows Service с автостартом
nssm install Qdrant "$env:USERPROFILE\scoop\apps\qdrant\current\qdrant.exe" `
  "--storage-path C:\Users\whoami\qdrant_storage"

# Запуск сервиса
nssm start Qdrant
```

После этого Qdrant работает как служба Windows — автоматически запускается при загрузке системы.

Проверка:

```powershell
curl.exe -s http://localhost:6333/health
```

Ожидаемый ответ: `{"status":"ok","title":"qdrant","version":"..."}`

### Вариант 2 — бинарник + Task Scheduler

1. Скачай `qdrant-x86_64-pc-windows-msvc.zip` с https://github.com/qdrant/qdrant/releases/latest
2. Распакуй в `C:\qdrant\`
3. Создай задачу в Планировщике задач (Task Scheduler):
   - Триггер: `At startup`
   - Действие: запуск `C:\qdrant\qdrant.exe`
   - Аргументы: `--storage-path C:\Users\whoami\qdrant_storage`

---

## Перенос коллекций из Docker

Если Qdrant уже работал в Docker, коллекции лежат на хосте в примонтированной папке.

### macOS

По умолчанию Docker Desktop на Mac хранит данные внутри своей VM. Если ты использовал `-v /Users/you/qdrant_storage:/qdrant/storage`, то папка уже на хосте — просто укажи `--storage-path` на неё.

Если не монтировал volume, скопируй из Docker-контейнера:

```bash
docker cp qdrant-container:/qdrant/storage /Users/$(whoami)/qdrant_storage
```

### Windows

Если монтировал `-v C:\Users\you\qdrant_storage:/qdrant/storage` — папка уже на хосте.

Если нет:

```powershell
docker cp qdrant-container:/qdrant/storage C:\Users\whoami\qdrant_storage
```

---

## Обновление opencode.json

Qdrant по-прежнему на `localhost:6333` — менять `QDRANT_URL` не нужно. Просто убедись, что:

```json
"environment": {
    "QDRANT_URL": "http://localhost:6333"
}
```
