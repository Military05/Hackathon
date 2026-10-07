import asyncio
import hashlib
import time

from .errors import AgentError
from .loop import PROMPT_VERSION, run_analysis
from .providers import canonical
from .store import JobStore, WorkerLease


class AgentService:
    def __init__(self, config, provider, client):
        self.config, self.provider, self.client = config, provider, client
        self.store = JobStore(config.database, config)
        self.lease = WorkerLease(config.database)
        self.wake = asyncio.Event()
        self.worker = None

    def execution(self):
        return {"model_name": self.config.model, "provider": self.config.provider, "prompt_version": PROMPT_VERSION,
                "max_execution_seconds": self.config.max_execution_seconds,
                "max_model_requests": self.config.max_model_requests, "max_tool_calls": self.config.max_tool_calls}

    async def start(self):
        if self.worker is not None:
            return
        self.lease.acquire()
        try:
            await asyncio.to_thread(self.store.recover)
            self.worker = asyncio.create_task(self._work(), name="shared-local-dispatcher")
        except BaseException:
            self.lease.release()
            raise

    async def stop(self):
        try:
            if self.worker:
                self.worker.cancel()
                try:
                    await self.worker
                except asyncio.CancelledError:
                    pass
                self.worker = None
            await self.client.close()
        finally:
            self.lease.release()

    async def request(self, incident_id, operator_id):
        if self.worker is None or self.worker.done():
            raise AgentError("unavailable", "Agent worker is not running.", 503)
        snapshot = await self.provider.snapshot(incident_id)
        key = hashlib.sha256(canonical([incident_id, snapshot.snapshot_id, self.config.model,
                                        self.config.provider, PROMPT_VERSION]).encode()).hexdigest()
        cached = await asyncio.to_thread(self.store.cached, key, operator_id)
        if cached:
            return {**cached, "cached": True, "stale": False}
        # An outage cannot silently select another model or accept unserviceable work.
        await self.client.ensure_available()
        job, cached = await asyncio.to_thread(self.store.enqueue, snapshot, key, operator_id, self.execution())
        self.wake.set()
        return {**job, "cached": cached, "stale": False}

    async def get(self, job_id):
        job = await asyncio.to_thread(self.store.get, job_id)
        try:
            current = await self.provider.snapshot(job["incident_id"])
            job["stale"] = (current.snapshot_id != job["incident_snapshot"]["snapshot_id"]
                            or job["execution"] != self.execution())
        except AgentError as exc:
            if exc.code != "not_found":
                raise
            job["stale"] = True
        return job

    async def _work(self):
        current = None
        try:
            while True:
                self.wake.clear()
                item = await asyncio.to_thread(self.store.take_next)
                if item is None:
                    try:
                        await asyncio.wait_for(self.wake.wait(), timeout=0.5)
                    except TimeoutError:
                        pass
                    continue
                current, snapshot, execution = item
                try:
                    if execution != self.execution():
                        raise AgentError("configuration_changed", "Queued job belongs to a different model/prompt configuration; retry explicitly.")
                    result = await run_analysis(self.client, snapshot, self.config)
                    await asyncio.to_thread(self.store.finish, current, result=result)
                except AgentError as exc:
                    await asyncio.to_thread(self.store.finish, current, error=exc.as_dict())
                except Exception:
                    await asyncio.to_thread(self.store.finish, current,
                        error=AgentError("internal_error", "Analysis failed; no unverified result was published.", 500).as_dict())
                current = None
        finally:
            if current:
                await asyncio.to_thread(self.store.finish, current,
                    error=AgentError("interrupted", "Worker stopped during analysis.").as_dict())
