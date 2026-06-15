"""Reseta vídeos 'failed' que têm sp_media_code de volta para 'sp_media_created'."""
import json
import shutil
from pathlib import Path

path = Path("data/manifest.json")
backup = path.with_suffix(".json.bak_before_reset")
shutil.copy2(path, backup)
print(f"Backup salvo em: {backup}")

with open(path, encoding="utf-8") as f:
    d = json.load(f)

reset = 0
for vid, v in d["videos"].items():
    if v.get("state") == "failed" and v.get("sp_media_code"):
        title = (v.get("title") or vid)[:65]
        print(f"  reset: {title}")
        v["state"] = "sp_media_created"
        v["last_error"] = None
        reset += 1

with open(path, "w", encoding="utf-8") as f:
    json.dump(d, f, ensure_ascii=False, indent=2)

print(f"\nTotal: {reset} videos resetados para sp_media_created")
