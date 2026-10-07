"""Launch the authenticated v5 application with the transferred ML/agent modules."""
import argparse
import os
from pathlib import Path

import uvicorn

from .config import load_env_file

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default=str(ROOT / ".env"))
    parser.add_argument("--port", type=int, default=8001)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("Порт должен быть в диапазоне 1..65535")
    os.chdir(ROOT)
    if not Path(args.env_file).is_file():
        parser.error("Нет .env: скопируйте .env.example в .env")
    load_env_file(args.env_file)
    os.environ["DISPATCH_ENABLE_AGENT"] = "1"
    os.environ["DISPATCH_ENABLE_ML"] = "1"
    os.environ["DISPATCH_ENABLE_AUTH"] = "1"
    for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
        os.environ.setdefault(name, "2")
    uvicorn.run("src.core.main:app", host="127.0.0.1", port=args.port, workers=1, proxy_headers=False)


if __name__ == "__main__":
    main()
