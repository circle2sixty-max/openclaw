# Terry Music

Private AI music generator for friends. It generates MP3 tracks with MiniMax music-2.6 and emails the result to the address entered in the form.

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/circle2sixty-max/openclaw)

## Local Run

```bash
python3 app.py
```

Open:

```text
http://127.0.0.1:5050
```

The local script reads secrets from environment variables. On this Mac, it can also bridge the previous Claude Code configuration from `~/Downloads/minimax_music_tool.py` so the local preview keeps working during migration.

## Render Deploy

Use the Docker service in this repository. Render should inject `PORT`; the app listens on `0.0.0.0`.

Set these environment variables in Render:

```text
MINIMAX_API_TOKEN
SMTP_USER
SMTP_PASSWORD
SMTP_HOST=smtp.gmail.com
SMTP_PORT=465
```

Recommended Render region: Singapore. It is the closest Render region to China while still usable from the UK. For better China reliability, attach a custom domain and test from a mainland China network before sharing widely.

## Notes

Each browser gets a local client id, so the Jobs panel only shows that browser's own jobs. This is not a login system; it is a lightweight privacy boundary for a private friends-only tool.
