# Music Speaks Structural Refactor Execution Plan

**Goal:** Rebuild Music Speaks safely on an isolated branch, preserving production while fixing title/lyrics flow, player UI, i18n, voice library, and cyberpunk styling.

**Approach:** Start with behavior-preserving baseline and small verified batches. Keep `main` untouched until local tests and manual browser checks pass. Prioritize the core generation chain before UI polish.

**Tools:** Python stdlib server, pytest, git branch, local browser/manual checks, Render deployment only after final approval.

---

## Batch 1: Protected Baseline + Title-before-Music Flow

- [x] Create isolated branch `music-speaks-structural-refactor-20260430-012833`.
- [x] Run current test suite to establish baseline: `python3 -m pytest -q`.
- [x] Add `/api/lyrics` response fields: `song_title`, `generated_title`, `title_error`.
- [x] Preserve user-entered title when generating lyrics.
- [x] Fill empty title input in frontend after lyrics generation.
- [x] Add regression tests proving lyrics helper returns title before `/api/jobs` music generation.
- [x] Re-run test suite: `78 passed`.

## Batch 2: Structure Map + Safe Module Split

- [x] Extract stable Python constants/helpers into small modules without changing API behavior.
  - `music_speaks/voice_data.py` — voice library, language helpers
  - `music_speaks/titles.py` — song title cleaning/generation
  - `music_speaks/lyrics.py` — lyrics cleaning/generation/validation + MiniMax API wrapper + voice clone/speech helpers
- [x] Add import compatibility so existing tests importing from `app` still pass.
- [x] Re-run `pytest` after each extraction: `78 passed`.

## Batch 3: Lyrics + Player Logic

- [x] Audit player state, lyrics panel, fullscreen lyrics, and job switching interactions.
- [x] Fix duplicate `timeupdate` handler leak in fullscreen lyrics modal.
- [x] Fix player not clearing when currently playing job is deleted.
- [x] Fix stale lyrics/title state across generated jobs.

## Batch 4: I18N Cleanup

- [x] Replace hard-coded `lang === "en" ? ... : ...` UI strings with I18N keys (recorder + player).
- [x] Fix mixed-language strings in Korean/Japanese/etc.
- [x] Add missing i18n keys for fullscreen lyrics, player controls, empty states.
- [x] Fix admin page accidentally using frontend `t()` function.
- [x] Fix date locale mapping for all supported languages.

## Batch 5: Voice Library + MiniMax Capability Mapping

- [~] Voice list uses cached MiniMax API response with `DEFAULT_SYSTEM_VOICES` fallback.
- [ ] Add metadata: language, preview support, use case, unavailable reason.
- [ ] Update UI labels without changing core color theme.

## Batch 6: Cyberpunk UI Upgrade

- [ ] Preserve existing green/dark color theme.
- [ ] Add cyberpunk styling through borders, glass, glow, grid/noise, sharper states.
- [ ] Upgrade player, lyrics panel, voice picker, and job cards.

## Batch 7: Final Testing + Deployment

- [x] Run unit/integration tests: `78 passed`.
- [x] Run local server and manual browser checks: `/`, `/api/health`, `/api/voice` all 200.
- [ ] Compare production-safe behavior after merge.
- [ ] Only after approval: merge/push and verify Render online deployment.
