#!/usr/bin/env python3
"""Start an offline workstation, run the browser smoke test, and clean up."""
import argparse
import hashlib
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from contextlib import ExitStack
from pathlib import Path
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_MODULE = "backend.tests.evidence"
BROWSER_TEST = ROOT / "backend/tests/evidence/smoke_browser.mjs"
MAC_CHROME = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")


def installed_binary(override, choices):
    configured = os.environ.get(override)
    for name in [configured] if configured else choices:
        path = shutil.which(name)
        if path:
            return str(Path(path).resolve())
    return None


def prerequisites():
    node = installed_binary("SMOKE_NODE", ["node"])
    chrome = installed_binary("SMOKE_CHROME", ["google-chrome", "google-chrome-stable", "chromium", str(MAC_CHROME)])
    vite = ROOT / "frontend/node_modules/vite/bin/vite.js"
    if not node or not chrome or not vite.is_file():
        raise RuntimeError("Require Node 22+, installed frontend dependencies, and Chrome/Chromium. "
                           "See README for prerequisite checks and SMOKE_NODE/SMOKE_CHROME.")
    return node, chrome, vite


def file_digest(path):
    try:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()
    except FileNotFoundError:
        return None


def saved_database_fingerprints():
    paths = {"default": ROOT / "backend/data/market.sqlite3"}
    if configured := os.environ.get("DATABASE_PATH"):
        paths["configured"] = Path(configured).resolve()
    return {
        label: {suffix or "database": file_digest(Path(str(path) + suffix))
                for suffix in ("", "-wal", "-shm", "-journal")}
        for label, path in paths.items()
    }


def local_ports():
    with ExitStack() as stack:
        sockets = [stack.enter_context(socket.socket()) for _ in range(2)]
        for sock in sockets:
            sock.bind(("127.0.0.1", 0))
        return [sock.getsockname()[1] for sock in sockets]


def start_process(command, cwd, env, log, processes):
    with log.open("w") as output:
        process = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                   stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
    processes.append(process)
    return process


def run_logged(command, env, log, processes, timeout):
    process = start_process(command, ROOT, env, log, processes)
    try:
        status = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired as error:
        raise RuntimeError(f"Timed out. See {log.name}.") from error
    if status:
        raise RuntimeError(f"Command exited {status}. See {log.name}.")


def stop_processes(processes):
    # Stop groups even if their leaders have exited, so the browser's children are covered.
    for process in reversed(processes):
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    for process in reversed(processes):
        try:
            process.wait(timeout=4)
        except subprocess.TimeoutExpired:
            pass
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=4)


def wait_ready(url, token, process, log_name):
    opener = build_opener(ProxyHandler({}))
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Server exited before readiness. See {log_name}.")
        try:
            with opener.open(f"{url}/health/smoke-ready", timeout=1) as response:
                if json.load(response) == {"token": token} and process.poll() is None:
                    return
        except (URLError, TimeoutError, ValueError):
            pass
        time.sleep(0.1)
    raise RuntimeError(f"Server did not return this run's fixture token. See {log_name}.")


def workstation(output):
    node, chrome, vite = prerequisites()
    before = saved_database_fingerprints()
    processes = []
    failure = None
    offline = {"YAHOO_FEED_ENABLED": "false", "YAHOO_STREAM_ENABLED": "false"}
    with tempfile.TemporaryDirectory(prefix="ist402-smoke-") as temporary:
        temp = Path(temporary)
        database = temp / "fixture.sqlite3"
        backend_port, frontend_port = local_ports()
        backend_url = f"http://127.0.0.1:{backend_port}"
        frontend_url = f"http://127.0.0.1:{frontend_port}"
        token = os.urandom(24).hex()
        env = {**os.environ, **offline, "DATABASE_PATH": str(database), "PAPER_EVIDENCE_DATABASE": str(database),
               "SMOKE_RUN_TOKEN": token, "VITE_BACKEND_TARGET": backend_url, "PYTHONDONTWRITEBYTECODE": "1",
               "NODE_DISABLE_COMPILE_CACHE": "1"}
        try:
            run_logged([sys.executable, "-m", f"{EVIDENCE_MODULE}.backtest_server", str(database)],
                       env, output / "fixture.log", processes, timeout=30)
            backend = start_process(
                [sys.executable, "-m", "uvicorn", f"{EVIDENCE_MODULE}.smoke_server:app",
                 "--host", "127.0.0.1", "--port", str(backend_port)],
                ROOT, env, output / "backend.log", processes)
            wait_ready(backend_url, token, backend, "backend.log")
            frontend = start_process(
                [node, str(vite), "--host", "127.0.0.1", "--port", str(frontend_port), "--strictPort"],
                ROOT / "frontend", env, output / "frontend.log", processes)
            wait_ready(frontend_url, token, frontend, "frontend.log")
            run_logged([node, str(BROWSER_TEST), frontend_url, backend_url, chrome,
                        str(temp / "chrome-profile"), str(output)],
                       env, output / "browser.log", processes, timeout=90)
        except (Exception, KeyboardInterrupt) as error:
            failure = str(error) or "Smoke test interrupted"
        finally:
            stop_processes(processes)
            processes.clear()

        if failure is None:
            try:
                run_logged([sys.executable, "-m", f"{EVIDENCE_MODULE}.smoke_database", str(database),
                            str(output / "database-results.json")],
                           env, output / "database.log", processes, timeout=30)
            except (Exception, KeyboardInterrupt) as error:
                failure = str(error) or "Database check interrupted"
            finally:
                stop_processes(processes)

    unchanged = before == saved_database_fingerprints()
    if not unchanged:
        failure = (failure + " " if failure else "") + "Saved database or SQLite sidecar changed during the run."
    result = {"result": "FAIL" if failure else "PASS", "failure": failure,
              "saved_database_files_unchanged": unchanged,
              "checked_database_locations": list(before),
              "existing_saved_databases": [label for label, hashes in before.items() if hashes["database"]],
              "offline_feed_workers": offline}
    (output / "smoke-result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(f"{result['result']}: {output}")
    if failure:
        print(failure, file=sys.stderr)
    return int(bool(failure))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "artifacts/smoke",
                        help="Parent directory for a fresh output folder per run")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output = Path(tempfile.mkdtemp(prefix="run-", dir=args.output_dir.resolve()))
    try:
        return workstation(output)
    except (OSError, RuntimeError) as error:
        (output / "smoke-result.json").write_text(json.dumps({"result": "FAIL", "failure": str(error)}) + "\n")
        print(f"FAIL: {error}\nArtifacts: {output}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    def terminate(_signum, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, terminate)
    sys.exit(main())
