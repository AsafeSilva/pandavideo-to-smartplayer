import json
with open("data/manifest.json") as f:
    d = json.load(f)
folders = set(v["folder_path"].split("/")[0] for v in d["videos"].values())
print(f"Total videos: {len(d['videos'])}")
print("Pastas raiz:")
for f in sorted(folders):
    print(f"  - {f}")
