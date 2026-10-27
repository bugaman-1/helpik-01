"""One small synthetic UI dataset. Never persisted as clinical records."""
from datetime import timedelta
from django.utils import timezone

PATIENTS = [
    {"code": "anna", "name": "Анна Соколова", "initials": "АС", "age": 32, "state": "attention", "status": "Требует внимания", "routing": "Обязательно", "reason": "В вымышленном сценарии получено прямое сообщение о небезопасном состоянии. Требуется разбор врачом; низкий общий балл его не отменяет.", "note": "Новое сообщение и изменение самоотчёта"},
    {"code": "mikhail", "name": "Михаил Орлов", "initials": "МО", "age": 41, "state": "normal", "status": "Есть новые ответы", "routing": "Желательно", "reason": "В вымышленном сценарии пациент просит обсудить дальнейшее наблюдение со специалистом. Это пример интерфейса, а не заключение алгоритма.", "note": "Опрос заполнен сегодня"},
    {"code": "elena", "name": "Елена Морозова", "initials": "ЕМ", "age": 28, "state": "empty", "status": "Пока нет данных", "routing": "Не сформировано", "reason": "Самоотчётов пока нет. Отсутствие данных не означает улучшение.", "note": "Ожидается первая запись"},
]
DOCTORS = [{"name": "Мария Волкова", "initials": "МВ", "specialty": "Лечащий врач · демо", "primary": True}, {"name": "Алексей Петров", "initials": "АП", "specialty": "Наблюдающий врач · демо", "primary": False}]
MEDICINES = [{"code": "morning", "time": "09:00", "name": "Демо-препарат", "dose": "1 условная единица", "note": "Пример утреннего пункта расписания"}, {"code": "evening", "time": "20:00", "name": "Демо-препарат", "dose": "1 условная единица", "note": "Пример вечернего пункта расписания"}]
PARAMETERS = [("mood", "Настроение"), ("sleep", "Сон"), ("energy", "Энергия")]


def entries(state="normal"):
    if state == "empty":
        return []
    today = timezone.localdate()
    labels = {"mood": ["Спокойнее", "Без изменений", "Труднее, чем обычно"], "sleep": ["Отдых восстановил силы", "Сон прерывался", "Трудно заснуть"], "energy": ["Хватает на обычные дела", "Усталость к вечеру", "Сил меньше обычного"]}
    return [{"id": f"sample-{offset}", "day": today-timedelta(days=offset), "time": "09:15" if offset % 2 else "19:30",
             "values": {k: v[(offset+1) % 3] for k, v in labels.items()}, "height": [52, 74, 40, 65, 48, 82, 61][offset % 7],
             "status": "Запись сохранена", "demo": True} for offset in [0, 1, 2, 4, 5, 7, 8, 10, 12, 14, 17, 20]]
