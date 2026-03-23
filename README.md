# iClassPro Enrollment Bot

This bot automates class enrollments on an iClassPro-powered portal. You can run it manually via a command-line interface or, for a much better experience, use the included web dashboard.

## 🚀 Web Dashboard (Recommended)

The easiest way to use the bot is through the web dashboard. It provides a full user interface for building schedules, managing credentials, and viewing live progress.

### Quick Start:

1.  Clone this repository.
2.  Run the dashboard script (this will handle first-time setup for you):
    ```bash
    ./run_dashboard.sh
    ```
3.  Open your browser to **http://localhost:8000**.

From there, you can configure everything visually!

---

## 🤖 Command-Line Usage

For advanced users or for integrating with other scripts.

### Basic Run

The helper script handles environment setup automatically:

```bash
./run_enrollment.sh
```

This script uses the schedule and credentials defined in your `.env` file (which is automatically created if it doesn't exist). You can also pass command-line arguments to override any setting:

```bash
# Example: Run with a specific schedule and complete the final transaction
./run_enrollment.sh --schedule schedules/my_custom_schedule.json --complete-transaction
```

*Note: The one-time environment setup (creating the virtual environment, installing dependencies, and installing the browser) is handled automatically the first time you run `run_dashboard.sh` or `run_enrollment.sh`.*

## ⚙️ Configuration

### Schedules (`schedules/` directory)

The bot uses JSON files to define which classes to enroll in. You can create as many as you like. The format is a list of classes:

```json
[
  {"Day": "Monday", "Location": "El Segundo", "Time": "5:45am"},
  {"Day": "Saturday", "Location": "Santa Monica", "Time": "10:00am"}
]
```

### Credentials (`.env` file)

Use the `.env` file to store sensitive values. This file is ignored by Git, so your secrets are safe. See `.env.example` for all available options.

## 🤖 Automation

For automated runs, you can call the `run_enrollment.sh` script from a cron job.

```bash
# Example: Run every Sunday at 8:00 PM
0 20 * * 0 /home/jmarcovici/repos/iclasspro-driver/run_enrollment.sh >> /home/jmarcovici/repos/iclasspro-driver/cron.log 2>&1
```

*(Note: Make sure to use the absolute path to your script in your crontab).*
