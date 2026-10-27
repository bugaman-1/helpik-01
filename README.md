# Helpik

Веб-приложение для наблюдения за самочувствием пациента: ежедневный опрос, динамика, назначения лекарств, сообщения и кабинет врача.

- `site/` — Django, интерфейс, миграции и тесты.
- `site/quiz/ml/sitec-v1.json` — параметры моделей для работы приложения без sklearn.
- `sitec/` — код обучения и экспорта моделей, схема вопросов. Обучающие данные не включены.

## Первоначальная настройка — Windows / PowerShell

Установите Python 3.12. Все команды выполняются из корня скачанного репозитория, где находятся `requirements.txt` и папка `site`.

Создайте виртуальное окружение, активируйте его и установите зависимости:

```powershell
.\your_venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```
Замените your venv на название вашего виртуального окружения.

Настройте отдельную локальную базу и создайте секретный ключ. Ключ сохранится только на вашем компьютере и будет использоваться при следующих запусках:

Подготовьте базу, первый аккаунт модератора, опрос и ключи уведомлений:

```powershell
python site/manage.py migrate
python site/manage.py bootstrap_moderator moderator --reason 'Первоначальная настройка'
python site/manage.py prepare_screening
python site/manage.py prepare_notifications
```

Пароль модератора задаётся при выполнении команды и не отображается. `bootstrap_moderator` нужен только при создании первого аккаунта.

Запустите приложение:

```powershell
python site/manage.py runserver 127.0.0.1:8000 --insecure
```

Откройте http://127.0.0.1:8000/ и войдите под созданным аккаунтом. Окно терминала оставляйте открытым; для остановки нажмите `Ctrl+C`. Флаг `--insecure` позволяет локальному серверу разработки отдавать стили и JavaScript при `DEBUG=False`.

## Повторный запуск

После закрытия терминала или перезагрузки компьютера откройте PowerShell в корне репозитория и выполните:

```powershell
.\.venv\Scripts\Activate.ps1
$env:APP_ENV = 'demo'
$env:DJANGO_DB_PATH = Join-Path (Get-Location) 'site/runtime/demo.sqlite3'
$env:DJANGO_SECRET_KEY = (Get-Content -LiteralPath 'site/runtime/django-secret.txt' -Raw).Trim()
python site/manage.py migrate
python site/manage.py runserver 127.0.0.1:8000 --insecure
```

Пересоздавать окружение, аккаунт модератора или ключ не нужно. После обновления `requirements.txt` повторите `python -m pip install -r requirements.txt`.

Для выхода из виртуального окружения после остановки сервера:

```powershell
deactivate
```

База, окружение и ключи исключены из Git. `.env.example` перечисляет доступные настройки; приложение читает переменные окружения процесса и не загружает `.env` автоматически.

## Тесты

В том же окружении:

```powershell
$env:APP_ENV = 'test'
python site/manage.py test quiz --noinput
```

Модели обучены на синтетических данных. Их результаты являются экспериментальными и не заменяют оценку врача.
