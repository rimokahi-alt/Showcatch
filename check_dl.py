import requests, json

r = requests.post('http://127.0.0.1:6800/jsonrpc', json={
    'jsonrpc': '2.0', 'id': '1', 'method': 'aria2.tellActive', 'params': []
}, timeout=3)
data = r.json()
for x in data.get('result', []):
    print(f"GID: {x['gid']}")
    print(f"  Speed: {x['downloadSpeed']} B/s")
    print(f"  Connections: {x['connections']}")
    print(f"  Peers: {x.get('connections', '?')}")
    bt = x.get('btMeta', {})
    print(f"  Metadata: {json.dumps(bt, indent=2) if bt else 'none'}")
