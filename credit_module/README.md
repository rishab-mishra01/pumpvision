# PumpVision Credit Module

Standalone credit management web app for Shree Petroleum (RO 206858).
Built with Flask, SQLAlchemy, and Tailwind CSS. Mobile-first.

## Quick Start (Local)

```bash
cd credit_module

# 1. Create and activate virtual environment
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # Mac/Linux

# 2. Install dependencies
pip install -r requirements.txt

# 3. Copy and fill in credentials
copy .env.example .env
# Edit .env with your passwords

# 4. Run the app
python app.py
```

The app runs at `http://0.0.0.0:5000`.

- Open on laptop: http://localhost:5000
- Open on phone (same WiFi): http://192.168.x.x:5000

## Credentials

Set in `.env`:

| Variable | Description |
|----------|-------------|
| `SECRET_KEY` | Flask session secret (any random string) |
| `DATABASE_URL` | SQLite path or PostgreSQL URL |
| `OWNER_USERNAME` | Owner login username |
| `OWNER_PASSWORD` | Owner login password |
| `ATTENDANT_USERNAME` | Attendant login username |
| `ATTENDANT_PASSWORD` | Attendant login password |

## Roles

- **Owner** → `/owner/dashboard` — full access to all screens
- **Attendant** → `/attendant/log` — credit transaction logging only

## Deploy to Render / Railway

1. Push the `pumpvision` repo to GitHub
2. Create a new Web Service pointing to this repo
3. Set **Root Directory** to `credit_module`
4. Set **Start Command** to `gunicorn credit_module.app:app`
5. Add environment variables: `SECRET_KEY`, `DATABASE_URL` (PostgreSQL URL), all credentials
6. The `Procfile` handles start command automatically on Railway

## Database

- **Local:** SQLite (`pumpvision_credit.db` created automatically on first run)
- **Production:** Set `DATABASE_URL=postgresql://...` — no code changes needed
- All tables created automatically on first run via `db.create_all()`
- Fuel prices seeded automatically if table is empty

## PDF Invoices

Uses WeasyPrint. On Windows, WeasyPrint requires GTK3 runtime.
If installation is complex, the PDF route falls back to returning HTML.
On Linux (Render/Railway), WeasyPrint works out of the box.
