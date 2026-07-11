"""
Qanuni.AI — Background Jobs Engine
===================================
A lightweight, thread-safe task worker queue for asynchronous tasks.
Used for document processing, indexing, and heavy legal analytics.
"""

import uuid
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Any, Callable

# Global job store
_jobs: Dict[str, Dict[str, Any]] = {}
_jobs_lock = threading.Lock()

# Thread pool with max 4 workers
_executor = ThreadPoolExecutor(max_workers=4)

def get_job_status(job_id: str) -> Dict[str, Any]:
    """Retrieve job details and state thread-safely."""
    with _jobs_lock:
        return _jobs.get(job_id, {"status": "not_found"})


def set_job_status(job_id: str, status: str, result: Any = None, error: str = None, progress: int = 0):
    """Update job state details."""
    with _jobs_lock:
        if job_id not in _jobs:
            _jobs[job_id] = {
                "id": job_id,
                "created_at": time.time(),
                "updated_at": time.time()
            }
        _jobs[job_id].update({
            "status": status,
            "result": result,
            "error": error,
            "progress": progress,
            "updated_at": time.time()
        })


def run_job_async(func: Callable, *args, **kwargs) -> str:
    """Submit a task to be processed by background workers. Returns job ID."""
    job_id = str(uuid.uuid4())
    set_job_status(job_id, "pending", progress=0)

    def task_wrapper():
        set_job_status(job_id, "running", progress=10)
        try:
            # Inject progress updating callback if function supports it
            if "progress_callback" in func.__code__.co_varnames:
                def cb(p):
                    set_job_status(job_id, "running", progress=p)
                kwargs["progress_callback"] = cb
            
            result = func(*args, **kwargs)
            set_job_status(job_id, "completed", result=result, progress=100)
        except Exception as e:
            print(f"[Jobs] Job {job_id} failed: {e}")
            set_job_status(job_id, "failed", error=str(e), progress=100)

    _executor.submit(task_wrapper)
    return job_id
