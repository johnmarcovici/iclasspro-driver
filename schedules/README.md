# Schedule Files Directory

This directory contains JSON schedule files for the iClassPro automation tool.

## File Naming Convention

- `schedule.json` - Default schedule file (used when no --schedule argument is provided)
- `default_schedule.json` - Original example schedule
- Custom schedules can use any name, e.g., `weekend_schedule.json`, `summer_schedule.json`

## Schedule Format

Each schedule file is a JSON array of objects with the following structure:

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

### Available Options

**Locations**: "El Segundo", "Santa Monica", "Culver", "VNSO"  
**Times**: "5:45am", "6:00am", "7:00am", "8:00am", "9:00am", "10:00am", "11:00am", "12:00pm"  
**Days**: "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"

## Usage

### Use Default Schedule
```bash
python iclasspro.py
```

### Use Custom Schedule
```bash
python iclasspro.py --schedule schedules/weekend_schedule.json
```

### Set Custom Schedule in .env
```bash
ICLASS_SCHEDULE=schedules/my_custom_schedule.json
```

## Creating Custom Schedules

1. Copy `default_schedule.json` to a new file
2. Edit the JSON with your desired classes
3. Use the new file with the `--schedule` argument or `ICLASS_SCHEDULE` environment variable