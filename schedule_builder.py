import pathlib
import tkinter as tk
import json

window = tk.Tk()

locations_times_dict = json.load(open("./times_locations.json", "r"))
locationvars = []
timevars = []
nextweekvar = tk.IntVar()
saveasdefaultvar = tk.IntVar()


def form_filename(file_stub: str = ""):
    return pathlib.Path(pathlib.Path(__file__).parent.resolve(), file_stub + ".json")


def destroy():
    window.destroy()


def reset():
    for k in range(len(locationvars)):
        locationvars[k].set(locations_times_dict["locations"][0])
        timevars[k].set(locations_times_dict["times"][0])


def process_schedule(output_schedule_filename: str = ""):
    schedule = []
    nextweek = " (Next Week)" if nextweekvar.get() else ""

    for k in range(len(locations_times_dict["days"])):
        location = locationvars[k].get()
        if location != locations_times_dict["locations"][0]:
            row = {
                "Location": locationvars[k].get(),
                "Time": timevars[k].get(),
                "Day": locations_times_dict["days"][k] + nextweek,
            }
            schedule.append(row)

    destroy()

    if len(schedule) > 0:
        print("Writing schedule to file '%s'" % output_schedule_filename)
        json.dump(schedule, open(output_schedule_filename, "w"), indent=2)
        if saveasdefaultvar.get():
            for row in schedule:
                row["Day"] = row["Day"].replace(nextweek, "")

            default_schedule_filename = form_filename(
                file_stub="local_default_schedule"
            )
            print("Updating default schedule to file '%s'" % default_schedule_filename)
            json.dump(schedule, open(default_schedule_filename, "w"), indent=2)


def main(schedule: str = ""):
    window.title("SCAQ Schedule Builder")
    window.geometry("1200x150")
    window.geometry("+450+300")

    frame = tk.Frame(window, highlightbackground="black", highlightthickness=1)

    # Read default schedule
    try:
        default_schedule = json.load(
            open(form_filename(file_stub="local_default_schedule"), "r")
        )
    except:
        default_schedule = json.load(
            open(form_filename(file_stub="default_schedule"), "r")
        )

    default_days = [d["Day"] for d in default_schedule]
    default_locations = [d["Location"] for d in default_schedule]
    default_times = [d["Time"] for d in default_schedule]

    for k in range(len(locations_times_dict["days"])):
        frame.columnconfigure(k, weight=1)

        # Add day of week label
        label = tk.Label(frame, text=locations_times_dict["days"][k])
        label.grid(row=0, column=k)

        if locations_times_dict["days"][k] in default_days:
            idx = default_days.index(locations_times_dict["days"][k])
            idx_location = locations_times_dict["locations"].index(
                default_locations[idx]
            )
            idx_time = locations_times_dict["times"].index(default_times[idx])
        else:
            idx_location = idx_time = 0

        # Add location menu
        v = tk.StringVar(frame, value=locations_times_dict["locations"][idx_location])
        w = tk.OptionMenu(frame, v, *locations_times_dict["locations"])
        w.config(width=30)
        w.grid(row=1, column=k)
        locationvars.append(v)

        # Add time menu
        v = tk.StringVar(frame, value=locations_times_dict["times"][idx_time])
        w = tk.OptionMenu(frame, v, *locations_times_dict["times"])
        w.config(width=30)
        w.grid(row=2, column=k)
        timevars.append(v)

    frame.pack(padx=5, pady=5, fill="x")

    # Add bottom frame for cancel and OK buttons
    frame = tk.Frame(window, highlightbackground="black", highlightthickness=1)

    # Form output schedule file name. Delete if it currently exists
    output_schedule_filename = (
        schedule if schedule else form_filename(file_stub="schedule")
    )
    pathlib.Path(output_schedule_filename).unlink(missing_ok=True)

    # Add cancel, reset, save-as, OK, and next week clickable features
    cancel_button = tk.Button(frame, text="Cancel", command=destroy, width=15)
    reset_button = tk.Button(frame, text="Reset", command=reset, width=15)
    saveasdefault_check = tk.Checkbutton(
        frame, text="Save as Default", variable=saveasdefaultvar, width=20
    )
    ok_button = tk.Button(
        frame,
        text="OK",
        command=lambda: process_schedule(output_schedule_filename),
        width=15,
    )
    nextweek_check = tk.Checkbutton(
        frame, text="Next Week", variable=nextweekvar, width=20
    )
    nextweek_check.select()

    cancel_button.pack(side="left")
    reset_button.pack(side="left")
    nextweek_check.pack(side="right")
    ok_button.pack(side="right")
    saveasdefault_check.pack(side="right")

    frame.pack(padx=5, pady=5, fill="x")
    window.mainloop()


if __name__ == "__main__":
    main()
