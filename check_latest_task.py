import os, glob, yaml, subprocess

tasks_dir = "/opt/veridian/ai-os/tasks"
dirs = sorted(glob.glob(os.path.join(tasks_dir, "*")), key=os.path.getmtime, reverse=True)
if not dirs:
    print("NO_TASKS_FOUND")
    raise SystemExit

latest = os.path.basename(dirs[0])
task_yaml = os.path.join(dirs[0], "task.yaml")
try:
    d = yaml.safe_load(open(task_yaml))
    status = d.get("status")
    cps = d.get("checkpoints", [])
    note = (cps[-1].get("note") or "")[:120] if cps else ""
except Exception as e:
    status, note = "UNKNOWN", str(e)[:100]

r = subprocess.run(
    ["systemctl", "--user", "list-units", "veridian-worker@*", "--state=active", "--no-legend"],
    capture_output=True, text=True,
)
active_count = len([l for l in r.stdout.splitlines() if l.strip()])

print(f"{latest}|{status}|{note}|active_units={active_count}")

if active_count == 0 and status not in ("completed",) :
    # stalled or newly-created-but-not-started -- restart it
    subprocess.run(["systemctl", "--user", "start", f"veridian-worker@{latest}.service"])
    print(f"AUTO_STARTED={latest}")
elif active_count == 0 and status == "completed":
    # check if a NEWER task dir exists that was self-dispatched but never started
    print("NOTE=latest task completed, no active unit -- check for self-dispatched next phase separately")
