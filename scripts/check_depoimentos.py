import json
with open("data/manifest.json") as f:
    d = json.load(f)
for vid_id, v in d["videos"].items():
    if "Depoimentos" in v.get("panda_folder_id", "") or "Depoimentos" in v.get("title", "") or "Depoimentos" in str(v):
        print(v)
# Better: check by folder path
print("\n--- Videos por pasta ---")
for vid_id, v in d["videos"].items():
    folder = v.get("folder", "") or v.get("panda_folder_path", "") or ""
    title = v.get("title", vid_id)
    state = v.get("state", "?")
    print(f"[{state}] {title[:80]}")
