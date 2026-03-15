# iClassPro
Automate enrollments for classes offered with an iClassPro portal.

**Note: This code has been rebuilt using Playwright for headless operation and autonomous execution.**

## Info
### Why Automate
I decided to automate enrollments because the normal process is click-intensive and time consuming. It works fine for adding a single class, but for recurring schedules I wanted something easier.

### Intended Users
This code is intended for use by anyone who is enrolling in classes from an iClassPro portal. However, it has only been tested with [SCAQ's iClassPro portal](https://app.iclasspro.com/portal/scaq). Controls have been exposed so that another team could work, provided it follows the same workflow in iClassPro.

### Why So Slow
The code processes enrollments pretty slowly (tens of seconds per class), because of pauses that are built in throughout the processing. These pauses exist to allow the iClassPro website pages to render and expose the buttons the tool will search for and click on.  

I would prefer to operate on an API from iClassPro, but at present none is offered, so instead this tool emulates a person using the website.

## Setup
### Create a virtual environment and install requirements  
```console
python -m venv venv && source ./venv/bin/activate && pip3 install -r requirements.txt
```

### Install Playwright browsers
After installing the requirements, install the Playwright browsers:

```console
playwright install chromium
```

This will download and install Chromium, which Playwright uses for automation.
### Configure Credentials
For security and autonomous operation, store your credentials in a `.env` file:

1. Copy the example file:
   ```console
   cp .env.example .env
   ```

2. Edit `.env` with your actual credentials:
   ```bash
   ICLASS_EMAIL=your@email.com
   ICLASS_PASSWORD=yourpassword
   ICLASS_STUDENT_ID=12345
   ICLASS_PROMO_CODE=YOURPROMOCODE  # Optional
   ```

**Security Note**: The `.env` file is automatically ignored by git to prevent accidental credential exposure.
## Run
### Create a Schedule
The code processes a schedule as described in a JSON file with one dict per enrollment (class instance). An [example schedule](./default_schedule.json) is included. Copy and modify this schedule to suit your needs.

Example schedule.json:
```json
[
  {
    "Location": "El Segundo",
    "Time": "12:00pm", 
    "Day": "Monday"
  },
  {
    "Location": "Culver",
    "Time": "12:00pm",
    "Day": "Tuesday"
  }
]
```

### Run the Automation
With credentials configured in `.env`, simply run:

```console
python iclasspro.py
```

Or override specific values:
```console
python iclasspro.py --schedule custom_schedule.json --next-week
```

Use `--help` to see all available options.

### Example Script
An example bash script [`run_enrollment.sh`](./run_enrollment.sh) is provided for easy automation. Edit it with your credentials and schedule it with cron.

## Autonomous Operation
This tool is designed to run headless and can be automated using cron jobs, scheduled tasks, or CI/CD pipelines.

### Using .env File (Recommended)
The easiest way for autonomous operation is to configure credentials in a `.env` file as described in Setup. Then you can run the script without any arguments.

### Example Cron Job (Linux/Mac)
Add this to your crontab (`crontab -e`) to run every Monday at 6:00 AM:
```bash
0 6 * * 1 cd /path/to/iclasspro-driver && source venv/bin/activate && python iclasspro.py
```

### Environment Variables (Alternative)
You can also set environment variables directly:
```bash
export ICLASS_EMAIL="your@email.com"
export ICLASS_PASSWORD="yourpassword"
export ICLASS_STUDENT_ID="12345"
python iclasspro.py
```

### Docker Container
For even more autonomous operation, you can containerize the application:
```dockerfile
FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
RUN playwright install chromium

COPY . .
CMD ["python", "iclasspro.py", "--email", "$EMAIL", "--password", "$PASSWORD", "--student-id", "$STUDENT_ID"]
```

where the arguments in brackets such as `<this argument>`
means fill it in with your specific values and remove the brackets.

### Combine Schedule Creation and Enrollment Steps
You can perform the schedule generation and enrollment addition in one step by including the argument `--build-schedule` as in

```console
python iclasspro.py --email <email address> --password <password> --student-id <student ID> --schedule schedule.json --build-schedule
```

### Promo Codes
iClassPro no longer automatically includes promo codes, so you need to provide them explicitly.

**Recommended**: Store your promo code in the `.env` file as `ICLASS_PROMO_CODE=YOURPROMOCODE`

**Alternative**: Pass as command line argument:
```console
python iclasspro.py --promo-code <promo code> <... all other args ...>
```


