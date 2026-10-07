"""Explicit fixture-only AI development server; mount build_router into A1 for production."""
import argparse
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from .api import build_router
from .config import AgentConfig, load_env_file
from .model_client import LocalModelClient
from .providers import FixtureProvider
from .service import AgentService


def create_fixture_app(fixture, config=None):
    config = config or AgentConfig.from_env()
    service = AgentService(config, FixtureProvider(fixture), LocalModelClient(config))

    @asynccontextmanager
    async def lifespan(app):
        await service.start()
        try:
            yield
        finally:
            await service.stop()

    app = FastAPI(title="B1 explicit fixture development server", lifespan=lifespan)
    app.include_router(build_router(service, lambda identifier: identifier in {"dispatcher-1", "dispatcher-2", "dispatcher-3"}))
    app.state.agent_service = service
    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", required=True, help="Explicit test data; this is not the main backend")
    parser.add_argument("--env-file")
    parser.add_argument("--port", type=int, default=8001)
    args = parser.parse_args()
    if args.env_file:
        load_env_file(args.env_file)
    uvicorn.run(create_fixture_app(args.fixture), host="127.0.0.1", port=args.port, workers=1)


if __name__ == "__main__":
    main()
