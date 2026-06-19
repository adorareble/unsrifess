You are a frontend developer tasked with revamping the UI of a multi-tenant menfess SaaS called "Fessable" (formerly Unsr!fess). The codebase is Vanilla HTML/CSS/JS — no framework. 7 HTML files, mobile-first. You will redesign the visual layer only: colors, typography, spacing, components. Do NOT change any API calls, auth logic, JS behavior, SSE handling, or server-side template injection variables (TENANT_SLUG, TENANT_X_SCREEN, TENANT_NAME).

---

## BRAND

Name: fessable (always lowercase in UI)
Logo mark: speech bubble with 3 colored dots (red · amber · teal)
Tone: clean, trustworthy, gen-z friendly

---

## DESIGN SYSTEM

### Font
Import Plus Jakarta Sans from Google Fonts. Add this in every file's <head>, before any <style> tag:

<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap" rel="stylesheet">

Apply globally:
body { font-family: 'Plus Jakarta Sans', system-ui, sans-serif; }

Replace ALL existing font-family declarations across all files with 'Plus Jakarta Sans', system-ui, sans-serif.

### Colors — replace ALL hardcoded color literals with these CSS variables.
Add this :root block at the top of every <style> tag:

:root {
  --bg-page:       #FAFAF9;
  --bg-surface:    #F1EFE8;
  --bg-card:       #FFFFFF;

  --text-primary:   #2C2C2A;
  --text-secondary: #888780;
  --text-hint:      #B4B2A9;

  --border:         #D3D1C7;

  --red:            #E24B4A;
  --red-tint:       #FAECE7;
  --red-text:       #A32D2D;

  --amber:          #EF9F27;
  --amber-tint:     #FAEEDA;
  --amber-text:     #854F0B;

  --teal:           #1D9E75;
  --teal-tint:      #EAF3DE;
  --teal-text:      #0F6E56;
}

### Typography
- Page title:    18px, weight 700, color var(--text-primary), letter-spacing -0.5px
- Section title: 14px, weight 600, color var(--text-primary)
- Body:          13–14px, weight 400, color var(--text-primary), line-height 1.5
- Label/caps:    10–11px, weight 600, letter-spacing 1.5px, uppercase, color var(--text-secondary)
- Hint/meta:     11px, weight 400, color var(--text-secondary)

### Spacing & Layout
- Mobile max-width: 480px (keep existing breakpoint)
- Page background: var(--bg-page)
- Horizontal padding: 16px
- Vertical gap between sections: 12–16px
- No shadows. Use background contrast + border instead.
- No gradients.

---

## COMPONENTS — redesign these across all files

### Top bar / header
- Background: var(--bg-page)
- Left: logo mark SVG (speech bubble + 3 dots) + "fessable" wordmark, 15px bold, var(--text-primary)
  SVG mark (inline, 24×24):
  <svg width="24" height="24" viewBox="0 0 64 64" xmlns="http://www.w3.org/2000/svg">
    <rect x="4" y="4" width="56" height="44" rx="14" fill="#F1EFE8" stroke="#D3D1C7" stroke-width="3"/>
    <path d="M14,48 L8,60 L26,48 Z" fill="#F1EFE8" stroke="#D3D1C7" stroke-width="3" stroke-linejoin="round"/>
    <line x1="15" y1="47.2" x2="25" y2="47.2" stroke="#F1EFE8" stroke-width="5"/>
    <circle cx="22" cy="26" r="6" fill="#E24B4A"/>
    <circle cx="32" cy="26" r="6" fill="#EF9F27"/>
    <circle cx="42" cy="26" r="6" fill="#1D9E75"/>
  </svg>
- Bottom border: 0.5px solid var(--border)
- Padding: 10px 16px

### Cards
- Background: var(--bg-card)
- Border: 0.5px solid var(--border)
- Border-radius: 14px
- Padding: 12px 14px
- No box-shadow

### Surface / secondary card
- Background: var(--bg-surface)
- Border-radius: 12px
- Padding: 10px 12px
- No border needed

### Status badges (keep existing class names: .badge, .status-pending, etc.)
- .status-pending / .badge.inactive:
    background: var(--amber-tint); color: var(--amber-text);
- .status-approved / .badge.active:
    background: var(--teal-tint); color: var(--teal-text);
- .status-rejected / .status-deleted:
    background: var(--red-tint); color: var(--red-text);
- All badges: font-size 10px, font-weight 600, padding 3px 10px, border-radius 9999px

### Buttons (keep existing IDs/classes, only restyle)
Primary CTA:
  background: var(--text-primary);
  color: var(--bg-page);
  border: none;
  border-radius: 12px;
  padding: 13px 16px;
  font-size: 14px;
  font-weight: 700;
  width: 100%;
  font-family: 'Plus Jakarta Sans', system-ui, sans-serif;

Accent / action button:
  background: var(--red);
  color: #fff;
  border: none;
  border-radius: 9999px;
  padding: 8px 18px;
  font-size: 12px;
  font-weight: 600;
  font-family: 'Plus Jakarta Sans', system-ui, sans-serif;

Ghost / secondary button:
  background: var(--bg-surface);
  color: var(--text-primary);
  border: 0.5px solid var(--border);
  border-radius: 9999px;
  padding: 7px 16px;
  font-size: 12px;
  font-family: 'Plus Jakarta Sans', system-ui, sans-serif;

### Inputs & textarea (keep existing IDs)
  background: var(--bg-surface);
  border: 0.5px solid var(--border);
  border-radius: 10px;
  padding: 10px 12px;
  font-size: 13px;
  font-family: 'Plus Jakarta Sans', system-ui, sans-serif;
  color: var(--text-primary);
  width: 100%;
  On focus: border-color: var(--text-primary); outline: none;
  Placeholder: color var(--text-hint)

### Bottom sheet modal (.popup-overlay + .popup-card)
  .popup-overlay: background rgba(0,0,0,0.4)
  .popup-card:
    background: var(--bg-card);
    border-radius: 20px 20px 0 0;
    border-top: 0.5px solid var(--border);
    padding: 20px 16px;
  Keep existing sheetUp animation.

### Toggle switch
  ON:  background var(--teal), knob white
  OFF: background var(--border), knob white
  Size: 40×22px, knob 18px, transition 0.2s

### Bottom navigation bar (panel-dashboard.html)
  Background: var(--bg-page)
  Border-top: 0.5px solid var(--border)
  4 items, icon 22px + label 10px
  Inactive: color var(--text-secondary)
  Active: color var(--red), font-weight 600

### Loading overlay
  Background: rgba(249,250,249,0.92)
  Spinner: border-color var(--border), border-top-color var(--red)
  Keep existing structure and JS hooks.

---

## FILE-SPECIFIC NOTES

### landing.html
- Replace hero text with: "your menfess base. fully automated."
- Subtext: "the auto menfess platform for base owners"
- Accent the 3 feature highlights with the red/amber/teal dot colors respectively
- Tenant list cards: use surface card style

### index.html (public submit form)
- Keep keyword validation logic intact — only restyle the input area
- Image upload zone: bg var(--bg-surface), border 0.5px dashed var(--border), radius 12px
- Preview grid: keep existing structure, restyle remove button to red pill
- Thread chunks: surface card, 12px font, muted line-count label
- SSE status banner: use teal-tint for connected, red-tint for error

### register.html
- Form card: white card on var(--bg-page)
- Keep all input IDs and form submission logic

### admin-login.html
- Center card, max-width 400px
- Logo mark centered at top
- Keep all auth logic and token storage

### panel-dashboard.html (admin SPA)
- 4 tabs → bottom nav bar (Queue, History, Users, Settings)
- Tab content: full-width surface cards
- Queue items: white card with status badge + approve/reject buttons
- Settings sub-tabs: keep as horizontal pill tabs inside the settings panel
- X Connect section: surface card with amber-tint warning note
- Branding upload: dashed border upload zone
- Keep ALL SSE event handlers, API calls, task_id polling logic

### admin-dashboard.html (root admin)
- Stats: 3-column grid of surface metric cards (number bold 18px, label muted 11px)
- Tenant table: replace with card list on mobile
- Keep all API calls, login-as logic, modal generation

---

## WHAT NOT TO CHANGE

- All fetch() / api() call patterns and URLs
- Auth token keys in localStorage: x_token, panel_token, panel_admin, admin_token, admin_role, tnc_accepted, visitor_id
- SSE event handler logic
- Server-side template injection placeholders: TENANT_SLUG, TENANT_X_SCREEN, TENANT_NAME
- URL rewriting patterns expected by public.py and panel.py
- All JS function names, IDs, and class names used as JS selectors
- Pagination logic
- Lightbox behavior
- Thread splitting logic
- The @media(max-width:500px) breakpoint
- Dead file panel-login.html — leave as-is

---

## DELIVERABLE

Revamp all 6 active HTML files (exclude panel-login.html). For each file:
1. Add Plus Jakarta Sans <link> import in <head> before any <style>
2. Replace the existing <style> block with the new design system
3. Add :root CSS variables at the top of the new <style>
4. Set body { font-family: 'Plus Jakarta Sans', system-ui, sans-serif; }
5. Restyle all components per spec above
6. Keep all HTML structure, IDs, JS, and API logic 100% intact