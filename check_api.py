import urllib.request, json
r = urllib.request.urlopen("http://127.0.0.1/api/status")
d = json.loads(r.read())
print("Risk:", json.dumps(d['risk'], indent=2))
print("Account:", json.dumps(d['account'], indent=2))
