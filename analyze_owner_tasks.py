import sqlite3

conn = sqlite3.connect('/opt/veridian/ai-os/memory/superboss-register.sqlite')

# Check what tables might reference owner-task IDs
numeric_id = '3689557'

# Check owner_priority_sequence
cursor = conn.execute("SELECT * FROM owner_priority_sequence WHERE owner_directive_id LIKE ? LIMIT 1", (f'%{numeric_id}%',))
result = cursor.fetchone()
if result:
    print(f"Found in owner_priority_sequence: {result}")
else:
    print("Not found in owner_priority_sequence")

# Check pm_decisions_pending
cursor = conn.execute("SELECT * FROM pm_decisions_pending WHERE entity_id LIKE ? LIMIT 1", (f'%{numeric_id}%',))
result = cursor.fetchone()
if result:
    print(f"Found in pm_decisions_pending: {result}")
else:
    print("Not found in pm_decisions_pending")

# Check wiring_registry
cursor = conn.execute("SELECT COUNT(*) as cnt FROM wiring_registry LIMIT 1")
result = cursor.fetchone()
print(f"wiring_registry table exists with {result[0]} row(s)")

conn.close()
