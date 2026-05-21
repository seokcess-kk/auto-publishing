# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Korean multi-platform content publishing automation. Pipelines pull from
content sources (Coupang/AliExpress products, Newspick articles, public data,
RSS feeds), generate posts via Claude/Gemini, and publish to WordPress, Tistory,
Naver, GitHub Pages, Twitter, Threads, Pinterest, and Instagram. A `schedule`-
based runner discovers pipelines via a `SCHEDULE` dict declared in each module
and runs them per `.env` cron-style times.

Read `README.md` for the user-facing setup walkthrough. This file is for
internal navigation only.

## Common Commands

```bash
# Activate venv first (Windows)
.venv\Scripts\activate

# Run a single pipeline (replaces module path)
python -u -m pipelines.coupang_to_tistory
python -u -m pipelines.newspick_to_threads
python -u -m pipelines.aliexpress_to_threads --keyword 무선이어폰

# Run the scheduler (long-lived; usually started by Task Scheduler instead)
python -m pipelines.scheduler_runner

# Bridge HTTP server alone (debugging — production is embedded in scheduler)
python -m pipelines.tistory_bridge

# Quick state probes (no test framework — these are the verifications)
python -m tools.reset_tistory_queue                  # unstick claimed/failed queue items
python -m tools.fix_threads_urls --dry-run           # diagnose then re-run without --dry-run

# One-off Bash health checks
Invoke-RestMethod http://localhost:5757/healthz
Invoke-RestMethod http://localhost:5757/captcha/state

# Manual session refresh (use when Aliexpress/Newspick login expires)
python tools/aliexpress_manual_login.py
python tools/newspick_manual_login.py
python -m scripts.tistory_manual_login

# Windows scheduled task management (admin PowerShell)
powershell -ExecutionPolicy Bypass -File tools\install_task_scheduler.ps1
powershell -ExecutionPolicy Bypass -File tools\install_chrome_autostart.ps1
Stop-ScheduledTask -TaskName AutoPublishing_Scheduler
Start-ScheduledTask -TaskName AutoPublishing_Scheduler
```

There is no test framework, no linter, no build step. Verification is done by
running a pipeline against the real services with diagnostic logging, then
checking `data/pipeline_runs.json` and `data/publish_queue.json`.

Windows console emits `cp949` and crashes on `—` / emoji output. Either
prepend `python -u` (and ensure scheduler wrapper sets `PYTHONIOENCODING=utf-8`),
or in Python scripts call `sys.stdout.reconfigure(encoding='utf-8')` early —
several `tools/*.py` already do this.

## Architecture

### Pipeline structure

Each pipeline is `pipelines/<source>_to_<target>.py` with this contract:

```python
SCHEDULE = {
    "env":  "SCHEDULE_FOO_BAR",         # cron times come from .env
    "func": "run",
    "args_from_env": ("CATEGORY:default", "POST_COUNT:1:int"),
}

def run(...) -> None:
    ...
```

`pipelines/scheduler_runner.py` discovers any module under `pipelines/` whose
`SCHEDULE` dict is defined, then schedules `_safe_subprocess_call(module_name)`
at every comma-separated time in the env var. Each slot runs as a fresh
`python -m <module>` subprocess (isolates Playwright / Chromium state),
captures stderr, and writes a record to `data/pipeline_runs.json` via
`common/run_ledger.py`.

Status detection: `proc.returncode == 0` alone is not enough — many pipelines
log `[ERROR]` but exit 0 (e.g. partial publish failure). The scheduler scans
stderr for `[ERROR]` lines and downgrades status to `failure` when no
`발행 성공/완료` marker is found. `common/run_diagnosis.py` then turns the
stderr tail into a one-line cause label for the daily summary.

Common kernels share publish loops:

- `pipelines/_kernel/product_wp.py` — Coupang/Aliexpress → WordPress
- `pipelines/_kernel/newspick.py` — Newspick → any single Publisher
- `pipelines/_riseset_common.py` — sunrise/sunset post body builder

The kernels accept a config object (`ProductWpConfig`, `NewspickConfig`) that
plugs in the source factory, publisher factory, and theme.

### Sources, publishers, common

- `sources/` modules pull data and return dicts. Keyword sources expose
  `get_next_keywords(n, categories=...)` against `data/keyword_pool.json`,
  with `mark_keywords_used()` to enforce no-duplication via
  `data/used_keywords.json`.
- `publishers/` implement `Publisher` (`publishers/base.py`) with `login()`
  and `post(title, content, tags, category, image_url, **kwargs)`. Tistory
  has two: `publishers/tistory.py` (web, persistent Chromium profile) and
  `publishers/tistory_bridge.py` (queue-only; see Bridge section).
- `common/` holds cross-cutting helpers — `notifier.py` (Telegram + Kakao,
  both fire-and-forget, neither blocks the pipeline), `kakao_token.py`
  (auto-refresh access_token using refresh_token from `.env`),
  `threads_token.py`, `tistory_blogs.py` (`make_publisher(blog_name)`
  dispatches to web or bridge publisher based on `TISTORY_PUBLISHER`),
  `tistory_queue.py` (bridge queue + captcha answer in-memory store),
  `ai_intro.py` (Claude CLI → Gemini fallback for intro/title/tags).

### Tistory DKAPTCHA bypass (Bridge + Extension)

The single biggest piece of architecture that requires reading multiple files
to understand. Tistory introduced server-side DKAPTCHA (Daum's Korean visual
captcha) on `/manage/post.json` around 2026-05-16, and the OAuth API was
retired in early 2024. There is no automated publish path. The workaround
runs the post-fill + publish-click step inside the user's regular Chrome via
a Manifest V3 extension, with the captcha relayed to the user's phone over
Telegram.

```
python pipeline (TistoryBridgePublisher.post)
  → common.tistory_queue.enqueue() → data/tistory_queue.json
                                              ▲
                                              │ GET /next (claim_next)
                                              │
pipelines.tistory_bridge HTTP server (port 5757, embedded as daemon thread
  in pipelines.scheduler_runner when TISTORY_PUBLISHER=bridge)
  ├── /next            extension polling endpoint
  ├── /captcha/needed  receives screenshot from extension → Telegram sendPhoto
  │                    + force_reply, stores tg_message_id in CAPTCHA_PENDING
  ├── /captcha/answer/<id>  extension polls; pops from CAPTCHA_ANSWERS
  ├── /done            marks queue done, records to publish_queue.json,
  │                    fires "Tistory 발행 완료" Telegram (only here, NOT
  │                    from the pipeline — that would be a false positive)
  └── /fail            marks queue failed
  Background thread: Telegram getUpdates long-poll; matches each reply's
  reply_to_message_id against CAPTCHA_PENDING and writes to CAPTCHA_ANSWERS

extension/  (user's regular Chrome, Manifest V3)
  ├── background.js       service worker; chrome.alarms polls /next every 30s
  │                       (Chrome 120+ minimum), opens new tab for claimed
  │                       items, chrome.tabs.captureVisibleTab for captcha,
  │                       chrome.webNavigation detects publish success
  ├── content.js          isolated world; fills title (textarea#post-title-inp),
  │                       tags (#tagText), clicks #publish-layer-btn → visibility
  │                       radio → #publish-btn; detects DKAPTCHA modal; relays
  │                       captcha to background; polls /captcha/answer
  ├── main_world.js       runs in "world": "MAIN" so it can touch window.tinymce
  │                       (isolated world cannot see page globals). Exposes
  │                       wait-tinymce / set-content RPC via window.postMessage.
  ├── captcha_frame.js    injected via "matches": ["https://dkaptcha.kakao.com/*"]
  │                       with all_frames:true because DKAPTCHA renders in a
  │                       cross-origin iframe (dkaptcha.kakao.com) and the parent
  │                       page cannot reach into its DOM. Receives fill-captcha
  │                       from background via chrome.tabs.sendMessage with
  │                       frameId, types the answer, clicks 답변 제출.
  └── popup.html/js       enable toggle + bridge healthz display + queue stats
```

Key gotchas in this flow (each was an actual debugging session):

- **content_script worlds**: `window.tinymce` is on the page's window, not the
  isolated-world content script's. Anything that touches page globals goes
  through `main_world.js` via `window.postMessage` RPC.
- **Cross-origin captcha iframe**: parent page cannot read `iframe.contentDocument`
  for `dkaptcha.kakao.com`. The fix is a *separate* content script registered
  for that origin with `all_frames: true`, talked to from background via
  `chrome.tabs.sendMessage(..., { frameId })`.
- **DKAPTCHA submit button disabled-state**: pasting `input.value` does not flip
  the framework's dirty/touched state and the "답변 제출" button stays
  disabled. `captcha_frame.js` simulates per-character `keydown`/`InputEvent`/
  `keyup` with `keyCode` patched in, then types → backspaces all → re-types
  to flip the touched flag.
- **`navigator.tabs.captureVisibleTab` permission**: requires `<all_urls>`
  host_permission. Specific tistory.com host permission is not enough.
- **Captcha URL after submit**: Tistory redirects to `/manage/posts`, not the
  new post's URL. `background.onTabNavigated` falls back to GET
  `https://<blog>.tistory.com/manage/posts.json` to recover the canonical
  post URL when the navigation lands on the list page.
- **TISTORY_BRIDGE_WAIT_SEC**: default 0 means the pipeline is fire-and-forget.
  It returns "queued" immediately; the Telegram "발행 완료" message comes
  from the bridge's `/done` handler, NOT from the pipeline. Pipelines skip
  their own `notify_pipeline_result` call in bridge mode for this reason.

### Threads publishing — permalink fetch

`publishers/threads.py` was constructing `https://www.threads.net/t/<numeric_id>`
from `threads_publish`'s id field, but that path expects an alphanumeric
shortcode and 404s on numeric IDs. `_publish_container` now calls
`GET /v1.0/{id}?fields=permalink` immediately after publish to retrieve the
canonical `@<username>/post/<shortcode>` URL. `tools/fix_threads_urls.py`
back-fills broken URLs already in `publish_queue.json`.

### Aliexpress keyword pool filtering

ItemScout's pool contains categories that have zero match on Aliexpress
(`식품` = food, `여가/생활편의` = leisure services, `도서` = Korean books,
`면세점` = duty-free, `기타` = misc). `pipelines/aliexpress_to_threads.py`
defines `ALIEXPRESS_CATEGORIES` whitelist and `get_ali_keywords(n)`, which
both `aliexpress_to_threads.py` and `aliexpress_to_tistory.py` use. Failed
keywords are pruned via `mark_keywords_used`, so the pool self-cleans over
time without further intervention.

### Newspick login

`sources/newspick.py` uses Kakao SSO. The popup flow needs `NEWSPICK_ID` /
`NEWSPICK_PW` in `.env` (same Kakao account as `TISTORY_EMAIL` /
`TISTORY_PASSWORD`). Missing these is what made every newspick pipeline
fail silently for ~a week — the error appears in stderr but exit code is 0,
which the scheduler now correctly downgrades to `status=failure`.

### Windows operations

Three scheduled tasks:

- `AutoPublishing_Scheduler` — runs `python -m pipelines.scheduler_runner` at
  startup with 1-minute delay; restarts up to 3 times on failure;
  `MultipleInstances IgnoreNew`. Registered by `tools/install_task_scheduler.ps1`.
  Bridge HTTP server is embedded as a daemon thread when
  `TISTORY_PUBLISHER=bridge`.
- `AutoPublishing_Watchdog` — runs `tools/watchdog.py` every 5 minutes;
  reads `.runtime/scheduler_heartbeat`; if stale >5 min, sends a Telegram
  alert and triggers `Stop-ScheduledTask` + `Start-ScheduledTask` to revive.
  All `RunLevel=Highest`, so `taskkill` from a non-elevated PowerShell will
  hit `Access denied` and the singleton guard's failure-to-kill list is
  what notifies that case.
- `AutoPublishing_Chrome` — runs `tools/chrome_background_launcher.ps1` at
  logon. The wrapper uses `WScript.Shell.Run` with `SW_SHOWMINNOACTIVE` (=7)
  so Chrome starts minimized without focus, and reads
  `%LOCALAPPDATA%\Google\Chrome\User Data\Local State` to auto-pick the
  user's `profile.last_used` (the one with the extension installed) —
  Chrome 116+ tends to ignore `--no-startup-window` and `--start-minimized`
  silently. Without this, the extension does not run when no Chrome window
  is open.

Orphan Playwright Chromium cleanup is built into
`common/browser_profile.py` `_kill_orphan_chromium_windows()`: when a parent
Python is force-killed, child Chromiums survive and lock
`.sessions/<name>_profile/`, so persistent_context launch fails with
"Target page, context or browser has been closed". The cleanup matches
`ExecutablePath like *ms-playwright*` to avoid touching the user's regular
Chrome at `Program Files\Google\Chrome`.

## Notable Files

| Path | Why it matters |
|------|----------------|
| `pipelines/scheduler_runner.py` | Single entrypoint for all scheduled work. Modify `_safe_subprocess_call` to change ledger / status / notification policy. |
| `pipelines/tistory_bridge.py` | Bridge HTTP server. Endpoints + Telegram long-poll thread both live here. `start_server_in_thread()` is what scheduler_runner imports for the embedded mode. |
| `common/tistory_queue.py` | Queue file format + in-memory captcha state. Bridge and `TistoryBridgePublisher` both import from here. |
| `common/tistory_blogs.py` | `make_publisher(blog_name)` returns web or bridge publisher based on `TISTORY_PUBLISHER`. All `*_to_tistory.py` pipelines must use this, not `TistoryPublisher` directly. |
| `extension/manifest.json` | Three content_script entries — one in MAIN world for `*.tistory.com/manage/newpost*`, one isolated for the same, one all_frames for `dkaptcha.kakao.com/*`. Host permissions include `<all_urls>` because `tabs.captureVisibleTab` requires it. |
| `publishers/threads.py` | `_publish_container` must call `_fetch_permalink` to get the canonical URL. Reverting that change re-introduces 404 URLs in publish_queue. |
| `common/notifier.py` | `_send_telegram` is reused by bridge for "발행 완료" messages. Pipeline-side `notify_pipeline_result` is skipped when `TISTORY_PUBLISHER=bridge` so users don't get false-positive "발행 성공" messages while the queue is still being processed. |
| `common/run_diagnosis.py` | Stderr-pattern → cause label mapping for the daily summary. Add patterns here when introducing new failure modes. |
| `tools/probe_*.py`, `tools/test_dkaptcha_*.py`, `tools/capture_editor_publish.py` | Debugging artifacts from the DKAPTCHA investigation. Useful templates for future "what is Tistory doing now" debugging — they demonstrate Playwright + page.evaluate + frame-level inspection patterns. |
