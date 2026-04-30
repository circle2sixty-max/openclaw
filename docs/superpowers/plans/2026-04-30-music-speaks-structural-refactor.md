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

- [ ] Extract stable Python constants/helpers into small modules without changing API behavior.
- [ ] Add import compatibility so existing tests importing from `app` still pass.
- [ ] Re-run `pytest` after each extraction.

## Batch 3: Lyrics + Player Logic

- [ ] Audit player state, lyrics panel, fullscreen lyrics, and job switching interactions.
- [ ] Fix stale lyrics/title state across generated jobs.
- [ ] Add browser-visible regression checks where possible.

## Batch 4: I18N Cleanup

- [ ] Generate full key coverage report across I18N languages.
- [ ] Replace hard-coded `lang === "en" ? ... : ...` UI strings with I18N keys.
- [ ] Fix mixed-language strings in Korean/Japanese/etc.
- [ ] Test language switching and visible text consistency.

## Batch 5: Voice Library + MiniMax Capability Mapping

- [ ] Compare current voice list with MiniMax official music/speech model capabilities.
- [ ] Add metadata: language, preview support, use case, unavailable reason.
- [ ] Update UI labels without changing core color theme.

## Batch 6: Cyberpunk UI Upgrade

- [ ] Preserve existing green/dark color theme.
- [ ] Add cyberpunk styling through borders, glass, glow, grid/noise, sharper states.
- [ ] Upgrade player, lyrics panel, voice picker, and job cards.

## Batch 7: Final Testing + Deployment

- [ ] Run unit/integration tests.
- [ ] Run local server and manual browser checks.
- [ ] Compare production-safe behavior.
- [ ] Only after approval: merge/push and verify Render online deployment.
