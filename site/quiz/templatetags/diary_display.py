from datetime import date
from django import template
from django.utils.formats import date_format

register = template.Library()


@register.filter
def readable_date(value):
    try:
        day = date.fromisoformat(value) if isinstance(value, str) else value
        return date_format(day, 'j E Y')
    except (ValueError, TypeError, AttributeError):
        return 'Дата не указана'
