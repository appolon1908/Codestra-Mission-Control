from mission_control.api_catalog import APICatalog
from pathlib import Path
Path("openapi/agent-brain.openapi.json").parent.mkdir(parents=True,exist_ok=True)
Path("openapi/agent-brain.openapi.json").write_text(APICatalog().json()+"\n")
