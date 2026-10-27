# Helpik

Веб-приложение для наблюдения за самочувствием пациента: ежедневный опрос, динамика, назначения лекарств, сообщения и кабинет врача. Интерфейс рассчитан на мобильные устройства.

- `site/` — Django, интерфейс, миграции и тесты.
- `site/quiz/ml/sitec-v1.json` — параметры моделей для работы приложения без sklearn.
- `sitec/` — код обучения и экспорта моделей, схема вопросов. Обучающие данные не включены.

## Запуск

Нужен Python 3.12. Из корня проекта в PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:APP_ENV = 'demo'
$env:DJANGO_SECRET_KEY = & .\.venv\Scripts\python.exe -c 'import secrets; print(secrets.token_urlsafe(48))'
.\.venv\Scripts\python.exe site/manage.py migrate
.\.venv\Scripts\python.exe site/manage.py bootstrap_moderator moderator --reason 'Первоначальная настройка'
.\.venv\Scripts\python.exe site/manage.py prepare_notifications
.\.venv\Scripts\python.exe site/manage.py runserver 127.0.0.1:8000 --insecure
```

Открыть http://127.0.0.1:8000/. Пароль модератора задаётся при создании. База и ключи создаются заново и исключены из Git. Для постоянного сервера сохраняйте `DJANGO_SECRET_KEY` вне репозитория; `.env.example` перечисляет настройки, которые приложение читает из окружения процесса.

## Тесты

В том же окружении:

```powershell
$env:APP_ENV = 'test'
.\.venv\Scripts\python.exe site/manage.py test quiz --noinput
```

Модели обучены на синтетических данных. Их результаты являются экспериментальными и не заменяют оценку врача.
