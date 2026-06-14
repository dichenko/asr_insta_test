# Instagram Insights Telegram Bot MVP

FastAPI MVP for connecting a Telegram user to an Instagram Professional account, fetching available Instagram data, generating an OpenAI report, and sending it back in Telegram.

## What changed from the original spec

- Telegram uses polling, not webhook.
- `TELEGRAM_WEBHOOK_SECRET` is not used.
- `TELEGRAM_BOT_USERNAME` is optional and only affects the success-page link back to Telegram.
- `TOKEN_ENCRYPTION_KEY` is required. It is a Fernet symmetric key used to encrypt Instagram access tokens before saving them in PostgreSQL.

## Environment

Create `.env` from `.env.example`:

```bash
cp .env.example .env
```

Generate `TOKEN_ENCRYPTION_KEY`:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Required values:

- `TELEGRAM_BOT_TOKEN`
- `INSTAGRAM_CLIENT_SECRET`
- `OPENAI_API_KEY`
- `TOKEN_ENCRYPTION_KEY`

The default app URL is:

```text
https://api.instagram-ai.liven8n.site
```

The exact Meta redirect URI must be:

```text
https://api.instagram-ai.liven8n.site/auth/instagram/callback
```

Required Instagram scopes:

```text
instagram_business_basic,instagram_business_manage_insights
```

## Run locally

```bash
docker compose up --build
```

Health check:

```bash
curl http://127.0.0.1:8000/health
```

Expected:

```json
{"status":"ok"}
```

## VPS run

```bash
docker compose up -d --build
docker compose logs -f app
```

Caddy should proxy:

```text
https://api.instagram-ai.liven8n.site -> 127.0.0.1:8000
```

Example Caddy block:

```caddy
api.instagram-ai.liven8n.site {
    reverse_proxy 127.0.0.1:8000
}
```

## Telegram testing

Polling starts automatically when `TELEGRAM_POLLING_ENABLED=true` and `TELEGRAM_BOT_TOKEN` is set.

In Telegram:

```text
/start
```

The bot should send a `Подключить Instagram` button.

## Meta dev mode testing

While the Meta app is in dev mode, test only with users added to the Meta app as testers/developers. The Instagram account must be a Professional account.

Test flow:

1. Send `/start` in Telegram.
2. Click `Подключить Instagram`.
3. Open the connect page.
4. Click `Connect with Instagram`.
5. Grant permissions.
6. Confirm callback reaches `/auth/instagram/callback`.
7. Confirm the encrypted token is saved.
8. Wait for the final report in Telegram.

## Logs and restart

```bash
docker compose logs -f app
docker compose restart app
docker compose down
```

Sensitive values are not intentionally logged: Instagram tokens, auth codes, app secret, OpenAI key, Telegram bot token.
