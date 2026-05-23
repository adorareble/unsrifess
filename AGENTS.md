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

## Deployment (Render.com Free Tier)
1. Push repo ke GitHub.
2. Di Render Dashboard → New Web Service → connect repo.
3. Render otomatis baca `render.yaml`.
4. Build & deploy. Butuh kartu kredit buat verifikasi akun (gratis).
5. Upload `twitter_state.json` via Render Shell atau upload manual.

**Catatan**: free tier Render spin down setelah 15 menit idle. Request pertama bakal lambat (cold start ~30-60 detik).

## Catatan
- Jangan commit tanpa explicit request.
- Update `AGENTS.md` kalau ada keputusan arsitektur penting.
