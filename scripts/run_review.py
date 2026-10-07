"""Run the review once and print a one-line summary. Cron: every 4 hours on the evo.
Needs DATABASE_URL (the wrapper takes it from ~/pumpvision-web.sh)."""
import os
import sys
from pathlib import Path

os.environ.setdefault("PUMPVISION_SKIP_BOOTSTRAP", "1")   # never create_all/migrate/seed from a job
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pumpvision import create_app  # noqa: E402
from pumpvision.services.review import run_review  # noqa: E402

app = create_app()
with app.app_context():
    s = run_review()
print(f"[review] {s['at']} open={s['open']} critical={s['critical']} new={s['new']} "
      f"resolved={s['resolved']} crashed={','.join(s['failed_checks']) or '-'}")
sys.exit(1 if s["failed_checks"] else 0)
