# iClassPro Enrollment Bot

A web-based tool that automates class sign-ups on iClassPro portals. Open the dashboard in your browser, enter your credentials once, and let it handle the rest.

## Getting Started

1. Clone this repository and open a terminal in the folder.
2. Run:
   ```bash
   ./run_dashboard.sh
   ```
3. Open **http://localhost:8000** in your browser.

That's it. The first time you run anything from the dashboard, your credentials are saved automatically and will be pre-filled next time you open it.

> **First run only:** The setup script will install the required dependencies and browser engine automatically. This takes a minute or two and won't happen again.

---

## Using the Dashboard

The dashboard has three sections:

### ⚙️ Configuration

Enter your iClassPro login email, password, and student ID here. You can also add a promo code if you have one.

There's also a toggle for **Complete Transaction** — leave this **off** while testing so you can verify everything looks right without being charged.

**Your settings are saved automatically** whenever you run a discovery or enrollment, so you only need to enter them once.

### 📋 Manual Enrollment

Build a list of classes you want to enroll in by picking the day, location, and time for each one. You can save and reload these lists, which makes it easy to re-use the same schedule.

When you're ready, hit **Run Enrollment Schedule** and watch the live output.

### 🔍 Discover & Enroll

Not sure which classes are available? Use this tab to browse what's open:

1. Optionally filter by day or location before searching.
2. Click **Discover Available Classes** — the tool will log in and pull the current availability.
3. Review the results in the table. Use the post-discovery filters to narrow down what's shown.
4. Check the classes you want, then click **Enroll Selected Classes**.

Enable **Get detailed links & instructor info** for richer results (takes a bit longer).

---

## Your Credentials & Privacy

Your login details are stored in a file called `.env` in this folder. It's created automatically the first time you run the tool and updated silently every time you use it — similar to how a website remembers you with a cookie.

This file is excluded from version control (`.gitignore`), so your credentials will never be accidentally shared if you push code changes.

---

## Running on a Schedule (Advanced)

If you want enrollments to run automatically at a specific time, you can set up a scheduled task. For example, to run every Sunday at 8:00 PM:

```bash
0 20 * * 0 /path/to/iclasspro-driver/run_enrollment.sh >> /path/to/iclasspro-driver/cron.log 2>&1
```

---

## For Technical Users

The dashboard is a thin wrapper around `iclasspro.py`, which can also be run directly from the command line. This is useful for scripting, automation, or running on a headless server without the web UI.

```bash
python3 iclasspro.py --help
```

Key flags at a glance:

| Flag | Description |
|---|---|
| `--schedule` | Path to a schedule JSON file |
| `--scrape` | Discover available classes instead of enrolling |
| `--scrape-days` | Comma-separated days to filter discovery (e.g. `Monday,Wednesday`) |
| `--scrape-locations` | Comma-separated locations to filter discovery |
| `--deep-scrape` | Fetch richer class details during discovery (slower) |
| `--complete-transaction` | Actually finalize the purchase |
| `--promo-code` | Apply a promo code at checkout |

All flags can also be set via environment variables in the `.env` file. Run `--help` for the full list.

---

## A Note on Safety

The **Complete Transaction** toggle controls whether the tool actually finalizes the purchase. When it's off, the bot will add classes to your cart but stop before checkout — useful for testing that it finds the right classes without any risk of being charged.
