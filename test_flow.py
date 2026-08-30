import requests, time

# Search
r = requests.post('http://127.0.0.1:8000/api/search', json={'query':'Matrix','media_type':'movie','_ip':'test2'})
rels = r.json().get('releases', [])
if not rels:
    print("No releases")
    exit()

best = rels[0]
print(f"Release: {best['title'][:60]}")
print(f"Peers backend test OK: {best['seeders']} seeders")

# Start download
r2 = requests.post('http://127.0.0.1:8000/api/download', json={
    'magnet': best['magnet'],
    'title': best['title'],
    'size': best['size'],
    'imdb_id': ''
})
dl = r2.json()
tid = dl.get('task_id')
print(f"Task: {tid}")

# Monitor 20s
for i in range(20):
    time.sleep(1)
    r3 = requests.get('http://127.0.0.1:8000/api/tasks')
    t = r3.json().get(tid, {})
    print(f"[{i+1:2d}s] peers={t.get('peers',0):3d}  speed={t.get('speed','?'):>12s}  progress={t.get('progress',0)}%  status={t.get('status','?')}")
    if t.get('status') in ('completed', 'error'):
        break

# Cleanup
requests.post(f'http://127.0.0.1:8000/api/download/{tid}/cancel')
print("Cancelled")
