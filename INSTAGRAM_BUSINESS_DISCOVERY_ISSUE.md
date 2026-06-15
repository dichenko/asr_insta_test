# Instagram Business Discovery: текущая проблема

Дата фиксации: 2026-06-15

## Кратко

В проект добавлен MVP-анализ конкурентов через Instagram Business Discovery. Пользователь успешно подключает свой Instagram Professional account, бот сохраняет OAuth access token и успешно получает данные/Insights собственного аккаунта. Однако запрос публичных данных конкурента через `business_discovery` падает с `HTTP 400`.

Проблема не в сохранении токена и не в конкретном аккаунте конкурента. Фактическая ошибка Meta говорит, что поле `business_discovery` не существует на текущем endpoint/API flow:

```text
Tried accessing nonexisting field (business_discovery)
type: IGApiException
code: 100
```

## Текущий OAuth/API flow

Сейчас подключение Instagram идет через Instagram Login:

```text
https://api.instagram.com/oauth/authorize
https://api.instagram.com/oauth/access_token
https://graph.instagram.com/v23.0/...
```

В настройках используются scopes:

```text
instagram_business_basic,instagram_business_manage_insights
```

После callback backend:

1. получает short-lived access token;
2. пытается обменять его на long-lived token;
3. получает профиль connected account через `graph.instagram.com`;
4. сохраняет токен в `instagram_accounts.access_token_encrypted`;
5. запускает анализ собственного аккаунта.

Токен хранится зашифрованным через Fernet, plain token в БД не сохраняется.

## Что работает

Собственный Instagram аккаунт подключается и анализируется. В логах были успешные запросы к media insights:

```text
GET https://graph.instagram.com/v23.0/{media_id}/insights?...&metric=reach "HTTP/1.1 200 OK"
GET https://graph.instagram.com/v23.0/{media_id}/insights?...&metric=views "HTTP/1.1 200 OK"
GET https://graph.instagram.com/v23.0/{media_id}/insights?...&metric=total_interactions "HTTP/1.1 200 OK"
GET https://graph.instagram.com/v23.0/{media_id}/insights?...&metric=saved "HTTP/1.1 200 OK"
GET https://graph.instagram.com/v23.0/{media_id}/insights?...&metric=shares "HTTP/1.1 200 OK"
```

Это подтверждает, что сохраненный пользовательский токен используется и валиден для текущего Instagram Login API.

## Что падает

После сообщения пользователя:

```text
@charlies_webs
```

бот отвечает:

```text
Принял: @charlies_webs. Собираю открытые данные аккаунта и готовлю краткий анализ.
Не удалось получить данные по этому аккаунту. Возможно, аккаунт приватный, не Business/Creator или Instagram не отдал данные через API.
```

В БД сохранена ошибка:

```text
id                  | 77d9e352-7b5c-42ec-a38f-387cbc05b2e3
competitor_username | charlies_webs
status              | failed
error_message       | HTTPStatusError: status_code=400 body={'error': {'message': 'Tried accessing nonexisting field (business_discovery)', 'type': 'IGApiException', 'code': 100, 'fbtrace_id': 'A_uDBLNC9dJVkg-_rDyLC4h'}}
created_at          | 2026-06-15 04:57:24.777221+00
finished_at         | 2026-06-15 04:57:25.037342+00
```

В `competitor_snapshots.api_errors_json`:

```json
[
  {
    "scope": "business_discovery",
    "error": "HTTPStatusError: status_code=400 body={'error': {'message': 'Tried accessing nonexisting field (business_discovery)', 'type': 'IGApiException', 'code': 100, 'fbtrace_id': 'A_uDBLNC9dJVkg-_rDyLC4h'}}"
  }
]
```

## Текущий запрос Business Discovery

Код делает запрос вида:

```text
GET https://graph.instagram.com/v23.0/{viewer_ig_user_id}
  ?fields=business_discovery.username(charlies_webs){id,username,name,biography,website,profile_picture_url,followers_count,follows_count,media_count,media.limit(25){id,caption,media_type,media_product_type,permalink,timestamp,like_count,comments_count}}
  &access_token={instagram_login_token}
```

Meta отвечает:

```text
HTTP/1.1 400 Bad Request
Tried accessing nonexisting field (business_discovery)
```

## Вывод

Business Discovery недоступен через текущий `graph.instagram.com` endpoint и Instagram Login token.

Для Business Discovery нужен другой flow: Instagram API with Facebook Login / Facebook Graph API. Запрос должен идти через Facebook Graph:

```text
https://graph.facebook.com/v23.0/{instagram_business_account_id}
  ?fields=business_discovery.username({competitor_username}){...}
  &access_token={facebook_or_page_access_token}
```

Документация Meta:

```text
https://developers.facebook.com/docs/instagram-platform/instagram-api-with-facebook-login/business-discovery/
```

## Что нужно изменить разработчикам

Нужно перевести подключение на Facebook Login / Meta flow:

1. Добавить Facebook OAuth login вместо текущего Instagram Login для сценария competitor analysis.
2. Запрашивать необходимые Facebook/Page/Instagram permissions.
3. После OAuth получить список Facebook Pages пользователя.
4. Для Page получить связанный Instagram Business/Creator account.
5. Сохранять в БД:
   - encrypted Facebook/Page access token;
   - Facebook Page ID;
   - Instagram Business Account ID, полученный через Facebook Graph;
   - scopes/permissions и срок действия токена.
6. Для Business Discovery использовать `graph.facebook.com`, а не `graph.instagram.com`.
7. Оставить текущий Instagram Login flow только для own-account MVP, если он нужен отдельно, либо полностью заменить его на Facebook Login, если все нужные Insights доступны через новый flow.

## Важное замечание по логам и секретам

До hotfix `5eaa4ee Sanitize HTTP error logging` библиотека `httpx` на уровне `INFO` писала полные URL запросов, включая:

- Instagram access token в query string;
- Telegram bot token в URL Telegram Bot API.

Hotfix отключил INFO-логи `httpx/httpcore` и добавил санитизацию тела ошибок. Но токены, уже попавшие в старые логи, нужно считать скомпрометированными.

Рекомендуемые действия:

1. Перевыпустить Telegram bot token через BotFather.
2. Переподключить Instagram аккаунт или отозвать старый Instagram token.
3. Проверить, что новые логи не содержат `access_token=` и `/bot<TOKEN>/`.

## Команды диагностики

Проверить последние competitor jobs:

```bash
docker compose exec postgres sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -x -c "select id, competitor_username, status, error_message, created_at, finished_at from competitor_analysis_jobs order by created_at desc limit 5;"'
```

Проверить snapshot errors:

```bash
docker compose exec postgres sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -x -c "select job_id, api_errors_json, created_at from competitor_snapshots order by created_at desc limit 5;"'
```

Проверить, что connected account token сохранен, не выводя сам токен:

```bash
docker compose exec postgres sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -x -c "select id, tg_id, instagram_user_id, username, account_type, token_expires_at, scopes, length(access_token_encrypted) as encrypted_token_len, connected_at from instagram_accounts order by connected_at desc limit 10;"'
```
