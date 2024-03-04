# iClassPro
Automate enrollments for classes offered with an iClassPro portal.

## Why?
I decided to automate the enrollment process because the manual (i.e. normal) process is fairly click-intensive and time consuming. It works fine for adding a single class from time to time, but for recurring schedules it can be a bit difficult.

## Who?
This code is intended for anyone who is enrolling in classes from an iClassPro portal. However, it has only been tested with [SCAQ's iClassPro portal](https://app.iclasspro.com/portal/scaq). Controls have been exposed so that another team could work, provided it follows the same workflow in iClassPro.

## Setup
### Create a virtual environment and install requirements  
```console
python -m venv venv && source ./venv/bin/activate && pip3 install -r requirements.txt
```

### Install google-chrome
Go here:
https://www.google.com/chrome/?platform=linux&hl=en

Press the Download Chrome button - it will download a .deb file, which you double-click to complete the installation.

You can verify the install and check the version with

```console
google-chrome-stable --version
```
You will need to install chromedriver (see next section) with the same major and minor release version, i.e. if the version is  

```console
Google Chrome 116.0.5845.140
```

you will need a chromedriver with version 116.0.5845.** where ** means any sub-version is OK.

### Install chromedriver
Go here:
https://googlechromelabs.github.io/chrome-for-testing

Locate the table holding the version matching closest to what version you installed for google-chrome (see previous section).

Locate the row that says chromedriver (not chrome), copy the URL, and paste into a new browser tab. It will download a zip file containing the chromedriver file. Copy that file to a location of your choosing.

## Run
### Specify your Login Credentials - One Time Only
Your login credentials (email, password, etc...) are specified in a file. Copy the provided template [credentials file](./default_credentials.json) to `credentials.json` and populate with your information. This file is ignored (with `.gitignore`) so it will not be saved to the repo.  

Your credentials don't normally change so this is a one-time setup step.

### Create a Schedule
The code processes a schedule as described in a json file with one dict per enrollment (class instance). An [example schedule](./default_schedule.json) is included.  

You can copy and modify this schedule to suit your schedule, or build a new one with  

```console
python schedule_builder.py
```

which saves the resulting schedule to `schedule.json`.  

You only need to update the schedule if it changes from your last session.

### Add Enrollments
Once you have a schedule you want to process, add enrollments with

```console
python iclasspro.py
```

### Combine Schedule Creation and Enrollment Steps
You can perform the schedule generation and enrollment addition in one step with

```console
python iclasspro.py --build-schedule
```



