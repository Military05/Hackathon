"""Import the global FastAPI application without opening any developer database."""
import atexit
import os
from pathlib import Path
import tempfile
from unittest.mock import patch

ADMIN_PASSWORD = "123456768"
_bootstrap = tempfile.TemporaryDirectory(prefix="dispatch-test-bootstrap-")
atexit.register(_bootstrap.cleanup)
with patch.dict(os.environ, {
    "DISPATCH_DB": str(Path(_bootstrap.name) / "bootstrap.db"),
    "DISPATCH_ENABLE_AUTH": "1",
    "DISPATCH_ENABLE_AGENT": "0",
    "DISPATCH_ENABLE_ML": "0",
    "DISPATCH_ENABLE_SIMULATOR": "0",
    "DISPATCH_ENABLE_DEMO_TRAFFIC": "0",
    "DEMO_AUTOSTART": "0",
}):
    from src.core.main import create_app
