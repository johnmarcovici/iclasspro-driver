# iClassPro
Automate class enrollments on an iClassPro portal using Playwright (headless).

## Quickstart
1) Create a virtual environment and install dependencies:
```bash
python -m venv venv && source ./venv/bin/activate && pip install -r requirements.txt
```
2) Install Playwright browsers:
```bash
playwright install chromium
```
3) Configure credentials using `.env`:
```bash
cp .env.example .env
# edit .env with real values
```
4) Run:
```bash
python iclasspro.py
```

## Schedule Files (schedules/)
- Default schedule: `schedules/schedule.json`
- Template schedule: `schedules/template_schedule.json` (copy/modify to create your own)

You can create your own schedules, then run with:
```bash
python iclasspro.py --schedule schedules/my_schedule.json
```

### Schedule JSON format
```json
[
  {"Location": "El Segundo", "Time": "12:00pm", "Day": "Monday"}
]
```

## Credentials & Promo Codes
Use `.env` to store sensitive values (ignored by git):
```bash
ICLASS_EMAIL=you@example.com
ICLASS_PASSWORD=supersecret
ICLASS_STUDENT_ID=12345
ICLASS_PROMO_CODE=MYCODE  # optional
```

You can also override values at runtime via command line args (see `--help`).

## Automation
- Use `run_enrollment.sh` as an example automation script.
- Schedule with cron, e.g.:
```bash
0 6 * * 1 cd /path/to/iclasspro-driver && source venv/bin/activate && python iclasspro.py
```

## Notes
- This tool uses Playwright and runs headless.
- It works by automating browser actions (no official API).


