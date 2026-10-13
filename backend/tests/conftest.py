import os
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "app" / "runtime"))
# isolate run storage from the developer's workspace and never pick up a real key
os.environ["WORKSPACE_DIR"] = tempfile.mkdtemp(prefix="lca-tests-")
os.environ["GROQ_API_KEY"] = ""
os.environ["LLM_PROVIDER"] = "none"

SAMPLE = BACKEND.parent / "samples" / "legacy_bank"
