from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()


def page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(
        f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <style>
    body {{ font-family: Arial, sans-serif; max-width: 760px; margin: 48px auto; padding: 0 20px; line-height: 1.5; }}
    h1 {{ font-size: 28px; }}
  </style>
</head>
<body><h1>{title}</h1>{body}</body>
</html>"""
    )


@router.get("/privacy", response_class=HTMLResponse)
async def privacy() -> HTMLResponse:
    return page(
        "Privacy Policy",
        """
<p>Сервис собирает Telegram ID, данные подключенного Instagram Professional аккаунта, доступные Insights и последние публикации.</p>
<p>Данные используются только для подготовки аналитического отчета и проверки MVP-сценария.</p>
<p>Access token Instagram хранится в зашифрованном виде. Секреты и токены не публикуются и не передаются третьим лицам, кроме API Instagram и OpenAI для выполнения анализа.</p>
<p>Для удаления данных напишите владельцу проекта и укажите Telegram аккаунт, с которого выполнялось подключение.</p>
""",
    )


@router.get("/terms", response_class=HTMLResponse)
async def terms() -> HTMLResponse:
    return page(
        "Terms",
        """
<p>Это MVP для проверки сценария анализа Instagram Insights. Сервис предоставляется без гарантий доступности, полноты данных и коммерческого результата.</p>
<p>Пользователь должен подключать только свой Instagram Professional аккаунт или аккаунт, на подключение которого у него есть разрешение.</p>
""",
    )


@router.get("/delete-data", response_class=HTMLResponse)
async def delete_data() -> HTMLResponse:
    return page(
        "Data Deletion",
        """
<p>Чтобы запросить удаление данных, напишите владельцу проекта и укажите Telegram аккаунт, с которого был подключен Instagram.</p>
<p>После обработки запроса будут удалены связанные токены, snapshots и отчеты.</p>
""",
    )
