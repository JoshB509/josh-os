"""Print the loaded-function hashes for the approval-lineage fix from the source on disk, to compare with what the
running server reports in Data/diagnostics/approval-lineage.json. Read only.  python tests/lineage_fingerprint.py"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from studio.lineage_diagnostic import fingerprints  # noqa: E402

print(json.dumps(fingerprints(), indent=1))
