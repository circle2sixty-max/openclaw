# Terry Music

Private AI music generator for friends. It generates MP3 tracks with MiniMax music-2.6 and provides a direct download button when the track is ready.

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
```

Recommended Render region: Singapore. It is the closest Render region to China while still usable from the UK. For better China reliability, attach a custom domain and test from a mainland China network before sharing widely.

Email delivery is optional. Render Free web services can block outbound SMTP ports, so the deploy flow is download-first by default.

For compatibility with older local setup, the app also accepts `MINIMAX_API_TOKEN` and passes it through to `mmx-cli` as `MINIMAX_API_KEY`.

## Admin

The admin page lists all jobs from all browsers and provides admin download links:

```text
/admin?key=YOUR_ADMIN_KEY
```

If `ADMIN_KEY` is not set, the app derives a stable admin key from the MiniMax key. This is convenient for a private tool, but a dedicated `ADMIN_KEY` is better before sharing widely.

## Lyrics Helper

Users can either paste finished lyrics or describe the lyrics they want in their own language. The Generate Lyrics button calls the MiniMax text model, constrains the response to song lyrics only, and fills the finished lyrics box so users can edit before generating music. If users skip that button, Terry Music can still generate lyrics during music creation when a lyrics brief is present.

## Song Titles

Users can provide a song title. If they leave it empty, Terry Music asks the MiniMax text model to create a concise title from the lyrics. The downloaded MP3 uses the title as its file name.

## Drafts

The form is draft-first: generating music does not clear the user's inputs. The browser saves the current draft locally, and the app also creates a draft URL with a `draft` id so the same draft can be restored in another window or browser when that URL is opened.

## Notes

Each browser gets a local client id, so the Jobs panel only shows that browser's own jobs. This is not a login system; it is a lightweight privacy boundary for a private friends-only tool.

Render Free storage is not a permanent archive. Generated MP3 files remain available while the service filesystem is alive, but a later restart or redeploy may remove files. Use R2/S3 or another object store if every generated track must be kept permanently.
