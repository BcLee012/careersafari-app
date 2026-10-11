"""Download/reuse the official full archive and build a reproducible local index."""
import argparse
import json
from pathlib import Path
import requests
from careersafari import onet

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", default=str(onet.DEFAULT_DIR / "db_31_0_csv.zip"))
    parser.add_argument("--index", default=str(onet.index_path()))
    args = parser.parse_args()
    archive = Path(args.archive)
    if not archive.exists():
        archive.parent.mkdir(parents=True, exist_ok=True)
        response = requests.get(onet.DOWNLOAD_URL, timeout=120)
        response.raise_for_status()
        if len(response.content) > 50_000_000:
            raise ValueError("Archive too large")
        archive.write_bytes(response.content)
    manifest = onet.import_snapshot(archive, args.index)
    print(json.dumps({k: v for k, v in manifest.items() if k != "tables"}, ensure_ascii=False, indent=2))
