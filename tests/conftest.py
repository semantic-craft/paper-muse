import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from knowledge_storm.utils import load_api_key

load_api_key(toml_file_path=str(ROOT / "secrets.toml"))
