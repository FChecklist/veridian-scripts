import json, os, re, glob, subprocess, sys
from datetime import datetime, timedelta, timezone

ROOT = os.path.expanduser("~/.claude/projects")
CUTOFF_DAYS = 30
OUT_DIR = "/opt/veridian/ai-os/session_metadata"
OUT_FILE = os.path.join(OUT_DIR, "WORK_IN_PROGRESS_METADATA.json")
LOG_FILE = os.path.join(OUT_DIR, "sync_log.txt")

FILE_TOOLS = {"Read", "Write", "Edit", "NotebookEdit"}
COMPLETE_MARKERS = re.compile(r"\b(merged|MERGED|PASS|complete|completed|done|closed|fixed and verified|shipped)\b")
TAG_RULES = [
    ("database", re.compile(r"\.sql\b|drizzle|migration|schema", re.I)),
    ("backend", re.compile(r"/api/|service\.ts|route\.ts|\.py\b", re.I)),
    ("ui", re.compile(r"\.tsx\b|\.jsx\b|component|dashboard", re.I)),
    ("ci-cd", re.compile(r"\.yml\b|workflow|github actions|ci\.yml", re.I)),
    ("audit", re.compile(r"\baudit\b|AUDIT:|z\.ai", re.I)),
    ("infra", re.compile(r"/opt/veridian|systemd|cron|tmux|ssh|server", re.I)),
    ("crm", re.compile(r"\bcrm\b", re.I)),
    ("pm-platform", re.compile(r"\bpms_|project management|projexa", re.I)),
    ("security", re.compile(r"secret|credential|token|password|leak", re.I)),
    ("documentation", re.compile(r"\.md\b|MASTER_INDEX|documentation", re.I)),
]

def log(msg):
    line = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")

def parse_ts(s):
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None

def extract_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""

def process_session(path, cutoff):
    project_dir = os.path.basename(os.path.dirname(path))
    session_id = os.path.splitext(os.path.basename(path))[0]
    try:
        mtime = datetime.fromtimestamp(os.path.getmtime(path), tz=timezone.utc)
    except Exception:
        return None
    if mtime < cutoff:
        return None

    files_touched, user_msgs = set(), []
    assistant_last_text, first_ts, last_ts = "", None, None
    n_user = n_assistant = n_tool_calls = 0
    pr_numbers = set()

    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                ts = parse_ts(obj.get("timestamp", "")) if obj.get("timestamp") else None
                if ts:
                    first_ts = ts if first_ts is None or ts < first_ts else first_ts
                    last_ts = ts if last_ts is None or ts > last_ts else last_ts
                msg = obj.get("message")
                if not isinstance(msg, dict):
                    continue
                role, content = msg.get("role"), msg.get("content")
                if role == "user":
                    n_user += 1
                    text = extract_text(content)
                    if text and len(user_msgs) < 4:
                        user_msgs.append(text[:400])
                    for m in re.findall(r"#(\d{3,5})\b", text or ""):
                        pr_numbers.add(int(m))
                elif role == "assistant":
                    n_assistant += 1
                    text = extract_text(content)
                    if text:
                        assistant_last_text = text[:400]
                    if isinstance(content, list):
                        for block in content:
                            if isinstance(block, dict) and block.get("type") == "tool_use":
                                n_tool_calls += 1
                                tinput = block.get("input", {}) or {}
                                if block.get("name") in FILE_TOOLS:
                                    fp = tinput.get("file_path") or tinput.get("path")
                                    if fp:
                                        files_touched.add(fp)
    except Exception as e:
        return {"session_id": session_id, "error": str(e)}

    return {
        "session_id": session_id, "project_dir": project_dir, "file_mtime": mtime.isoformat(),
        "first_ts": first_ts.isoformat() if first_ts else None, "last_ts": last_ts.isoformat() if last_ts else None,
        "n_user_msgs": n_user, "n_assistant_msgs": n_assistant, "n_tool_calls": n_tool_calls,
        "files_touched": sorted(files_touched)[:40], "files_touched_count": len(files_touched),
        "pr_numbers_mentioned": sorted(pr_numbers), "seed_user_msgs": user_msgs,
        "last_assistant_snippet": assistant_last_text,
    }

def clean_title(seed_msgs):
    for m in seed_msgs:
        m = re.sub(r"\s+", " ", m.strip())
        if len(m) > 15 and not m.startswith("<"):
            return m[:120]
    return "(untitled session)"

def derive_status(s):
    if COMPLETE_MARKERS.search(s.get("last_assistant_snippet") or ""):
        return "COMPLETED"
    if s.get("n_user_msgs", 0) <= 2 and s.get("n_tool_calls", 0) == 0:
        return "PENDING"
    return "IN_PROGRESS"

def derive_tags(s):
    haystack = " ".join(s.get("files_touched", [])) + " " + " ".join(s.get("seed_user_msgs", [])) + " " + (s.get("last_assistant_snippet") or "")
    return [name for name, pat in TAG_RULES if pat.search(haystack)] or ["general"]

def rebuild_local():
    cutoff = datetime.now(timezone.utc) - timedelta(days=CUTOFF_DAYS)
    all_jsonl = glob.glob(os.path.join(ROOT, "**", "*.jsonl"), recursive=True)
    top_level = [p for p in all_jsonl if "subagents" not in p.split(os.sep)]

    sessions = []
    for p in top_level:
        r = process_session(p, cutoff)
        if r:
            sessions.append(r)
    sessions.sort(key=lambda r: r.get("file_mtime", ""), reverse=True)

    tasks, total_files, completed, pending = [], set(), 0, 0
    for i, s in enumerate(sessions, 1):
        status = derive_status(s)
        completed += status == "COMPLETED"
        pending += status == "PENDING"
        files = s.get("files_touched", [])
        total_files.update(files)
        tasks.append({
            "id": f"TASK-{i:04d}", "title": clean_title(s.get("seed_user_msgs", [])), "status": status,
            "location": "SERVER", "tags": derive_tags(s), "files_involved": files,
            "context_summary": (
                f"[{s.get('project_dir')}] session {s.get('session_id')[:8]}, "
                f"{s.get('n_user_msgs')} user / {s.get('n_assistant_msgs')} assistant msgs, "
                f"{s.get('files_touched_count')} files touched. "
                f"Opened with: \"{(s.get('seed_user_msgs') or [''])[0][:200]}\". "
                f"Last known state: \"{(s.get('last_assistant_snippet') or '')[:200]}\""
            ),
            "session_id": s.get("session_id"), "first_ts": s.get("first_ts"), "last_ts": s.get("last_ts"),
        })

    out = {
        "meta": {
            "type": "HISTORICAL_ANALYSIS", "generated_on": datetime.now(timezone.utc).isoformat(),
            "days_scanned": CUTOFF_DAYS, "location": "SERVER (VERIDIAN-DEV)",
            "note": "title/status/tags are derived deterministically (keyword/heuristic rules), not LLM-summarized per session.",
        },
        "global_stats": {
            "total_sessions_found": len(sessions), "total_files_touched": len(total_files),
            "tasks_completed": completed, "tasks_pending": pending,
            "tasks_in_progress": len(sessions) - completed - pending,
        },
        "tasks": tasks,
    }
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    return out["global_stats"]

def main():
    log("=== sync run starting ===")
    try:
        stats = rebuild_local()
        log(f"local rebuild OK: {json.dumps(stats)}")
    except Exception as e:
        log(f"local rebuild FAILED: {e}")
    log("=== sync run complete (server does not push to laptop; laptop pulls/pushes on its own schedule) ===")

if __name__ == "__main__":
    main()
