# iClassPro
Automate enrollments for classes offered with an iClassPro portal.

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

Locate the table holding the version matching closest to what version you installed for google-chrome, and from within this table, locate the row that says **chromedriver** (not chrome). An example of a recent table with the pertinent row for a linux install is shown here:

<center>
    <img src = chromedriver-install-page.png/>
</center>

Copy the URL from the table and paste into a new browser tab. It will download a zip file containing the chromedriver file. Copy that file to the same folder where you checkout this repository.

## Run
### Create a Schedule
The code processes a schedule as described in a json file with one dict per enrollment (class instance), an [example of which](./default_schedule.json) is included. You can copy and modify this schedule to suit your schedule, or build a new one with  

```console
python schedule_builder.py
```

which saves the resulting schedule to `schedule.json`.  

You only need to update the schedule if it changes from your last session.

### Add Enrollments
Once you have a schedule you want to process, add enrollments with

```console
python iclasspro.py --email <email address> --password <password> --student-id <student ID> --schedule schedule.json
```

where the arguments in brackets such as `<this argument>`
means fill it in with your specific values and remove the brackets.

### Combine Schedule Creation and Enrollment Steps
You can perform the schedule generation and enrollment addition in one step by including the argument `--build-schedule` as in

```console
python iclasspro.py --email <email address> --password <password> --student-id <student ID> --schedule schedule.json --build-schedule
```

### Promo Codes
As of April 2025, iClassPro is automatically including your promo code, if you have one, so that this tool no longer needs to populate it
- However, if you find it is not being populated, supply an additional argument with

```console
python iclasspro.py --promo-code <promo code> <... all other args ...>
```


