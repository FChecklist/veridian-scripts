import sqlite3

conn = sqlite3.connect('/opt/veridian/ai-os/memory/superboss-register.sqlite')
conn.row_factory = sqlite3.Row

cursor = conn.execute("SELECT umr_id, task_identity, ts_submitted FROM umr_tasks WHERE status = 'running' AND task_identity LIKE 'owner-task-%' ORDER BY ts_submitted DESC LIMIT 5")
tasks = list(cursor.fetchall())

for task in tasks:
    print(f"{task['umr_id']} | {task['task_identity']} | {task['ts_submitted'][:10]}")

conn.close()
