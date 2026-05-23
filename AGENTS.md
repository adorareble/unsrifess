# AGENTS.md — TwitterTools

Unofficial X/Twitter tweet poster. Playwright browser automation + FastAPI web app.
No official Twitter API — session disimpan via `storageState`.

## Stack
- **Python 3.10+** — FastAPI, Uvicorn, Playwright

## Setup
```powershell
pip install -r backend/requirements.txt
playwright install chromium
```

## Login (sekali — admin)
```powershell
python setup_login.py
```
Browser terbuka → login manual ke x.com → session tersimpan di `twitter_state.json`.

## Dev server
```powershell
uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
```

## Struktur
```
backend/
  main.py              # FastAPI routes
  twitter_client.py    # Playwright wrapper (login, tweet, thread, upload)
  requirements.txt
frontend/
  index.html           # Compose form + char counter + image preview
setup_login.py         # One-time login script
twitter_state.json     # Generated (gitignored)
temp_images/           # Temp upload files (gitignored)
```

## Arsitektur & quirks
- **Thread splitting**: teks >280 chars dipotong per kalimat/paragraf, dipost sebagai reply chain via Playwright.
- **Image upload**: Playwright `expect_file_chooser` → `set_files` ke compose dialog.
- **Tweet URL**: di-capture dari response GraphQL `/CreateTweet` (intercept `page.on("response")`).
- **Session**: `storageState` Playwright simpan cookies + localStorage. Kalau expired → re-run `setup_login.py`.
- **Headless**: posting pake `headless=True`. Login pake `headless=False` (browser visible).
- **Gak ada dependencies lain**: no database, no npm, no build step.

## Deployment (Leapcell — serverless)
1. Push repo ke GitHub.
2. Di Leapcell Dashboard → New Service → connect repo (branch `main`).
3. **Env vars**:
   - `TWITTER_STATE_GZ` — base64(gzip(twitter_state.json)). Masukin dgn `Generate-TwitterStateGz.ps1`.
   - `STATE_DIR=/tmp`, `TEMP_DIR=/tmp/temp_images`
   - `PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS=1`
4. **Build command**:
   ```
   pip install -r requirements.txt && playwright install-deps chromium && playwright install chromium
   ```
5. **Start command**: `uvicorn backend.main:app --host 0.0.0.0 --port 8000`
6. **Serving port**: 8000

### Kirim session via env var
`twitter_state.json` gak bisa di-commit (public repo). Packing:
```powershell
# Generate-TwitterStateGz.ps1
$content = Get-Content -Raw twitter_state.json
$bytes = [System.Text.Encoding]::UTF8.GetBytes($content)
$ms = New-Object System.IO.MemoryStream
$gzip = New-Object System.IO.Compression.GzipStream($ms, [System.IO.Compression.CompressionMode]::Compress, $true)
$gzip.Write($bytes, 0, $bytes.Count); $gzip.Close()
[Convert]::ToBase64String($ms.ToArray()) | Set-Clipboard
```

Limit env var total 3KB → gzip dulu biar muat (~1.4KB).

## Arsitektur serverless
- **Gak pake task polling** — in-memory `tasks` dict gak persist antar request. Pake endpoint sync `/api/tweet-sync` langsung.
- **Frontend** (`index.html`) panggil `/api/tweet-sync`, loading sampe selesai.
- **Cold start** — request pertama lambat (~30-60 detik) karena Playwright + Chromium startup. Pastiin timeout cukup.

## Catatan
- Jangan commit tanpa explicit request.
- Update `AGENTS.md` kalau ada keputusan arsitektur penting.
