"""Runs many slow API calls at once instead of one after another."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from streamlit.runtime.scriptrunner import add_script_run_ctx, get_script_run_ctx

# Below requests' default connection pool size (10), and gentle enough for unofficial endpoints.
MAX_WORKERS = 8


def parallel_map(fn, items, max_workers: int = MAX_WORKERS) -> list:
    """fn(item) for every item, in order, run on a thread pool. A call that raises returns its
    exception in place of a result, so one failure doesn't sink the rest. Calls to st.cache_data
    functions inside fn fill the same cache the page reads from."""
    ctx = get_script_run_ctx()

    def run(item):
        add_script_run_ctx(ctx=ctx)
        try:
            return fn(item)
        except Exception as exc:  # noqa: BLE001 — returned to the caller to handle
            return exc

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        return list(pool.map(run, items))
