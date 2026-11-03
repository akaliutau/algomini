"""Bounded local process supervision for builds, coding, and codec workers."""

import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import tempfile
import threading
import time

from .common import worker_environment


SYSTEM_READ = ["/usr", "/lib", "/lib64", "/etc/ld.so.cache", "/proc/cpuinfo", "/proc/meminfo", "/dev/urandom"]


def kill_session(session_id):
    # Builders may use child process groups, but setsid is denied by seccomp.
    for path in Path("/proc").glob("[0-9]*/stat"):
        try:
            fields = path.read_text().rpartition(")")[2].split()
            if int(fields[3]) == session_id:
                os.kill(int(path.parent.name), signal.SIGKILL)
        except (OSError, ValueError, IndexError):
            continue


def run_isolated(argv, *, cwd, ro=(), rw=(), env=None, data=b"", timeout=30, memory_mib=256,
                 output_bytes=4 * 1024**2 + 65536, mode="worker", network=False):
    if mode not in ("worker", "baseline", "build", "coder") or (network and mode != "coder"):
        raise ValueError("Invalid isolation mode/network combination")
    # Open logs before restriction; they are the only inherited writable output descriptors.
    with tempfile.TemporaryDirectory(prefix="algomini-exec-") as temporary:
        directory = Path(temporary)
        ticket = directory / "job.json"
        ready_read, ready_write = os.pipe()
        start_read, start_write = os.pipe()
        job = {"argv": list(map(str, argv)), "cwd": str(Path(cwd).resolve()),
               "ro": SYSTEM_READ + list(map(str, ro)), "rw": ["/dev/null"] + list(map(str, rw)),
               "network": network, "mode": mode, "memory_bytes": memory_mib * 1024**2,
               "cpu_seconds": max(1, int(timeout) + 1), "output_bytes": output_bytes,
               "ready_fd": ready_write, "start_fd": start_read}
        ticket.write_text(json.dumps(job))
        launcher = Path(__file__).with_name("_isolate.py")
        with (directory / "stdout").open("w+b") as stdout, (directory / "stderr").open("w+b") as stderr:
            started = time.perf_counter()
            process = subprocess.Popen([sys.executable, "-I", str(launcher), str(ticket)],
                                       stdin=subprocess.PIPE, stdout=stdout, stderr=stderr,
                                       env=worker_environment() if env is None else env,
                                       start_new_session=True, close_fds=True, pass_fds=(ready_write, start_read))
            os.close(ready_write)
            os.close(start_read)
            completed = threading.Event()
            usage = {}

            def reap():
                _, status, resources = os.wait4(process.pid, 0)
                usage.update(finished=time.perf_counter(), peak_rss_kib=resources.ru_maxrss,
                             user_seconds=resources.ru_utime, system_seconds=resources.ru_stime)
                process.returncode = os.waitstatus_to_exitcode(status)
                completed.set()

            def send():
                try:
                    process.stdin.write(data)
                    process.stdin.flush()
                except (BrokenPipeError, OSError):
                    pass
                finally:
                    process.stdin.close()

            waiter = threading.Thread(target=reap)
            waiter.start()
            ready_at = None
            sender = None
            deadline = started + timeout
            try:
                # Only the trusted launcher owns this descriptor. It closes it
                # before exec; candidate stdout cannot forge the start marker.
                while time.perf_counter() < deadline and not completed.is_set():
                    readable, _, _ = select.select([ready_read], [], [], min(.05, max(0, deadline - time.perf_counter())))
                    if readable:
                        if os.read(ready_read, 1) == b"R":
                            ready_at = time.perf_counter()
                            os.write(start_write, b"S")
                            sender = threading.Thread(target=send)
                            sender.start()
                        break
                completed.wait(max(0, deadline - time.perf_counter()))
            finally:
                os.close(ready_read)
                os.close(start_write)
            timed_out = not completed.is_set()
            if timed_out:
                kill_session(process.pid)
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            waiter.join()
            # Clean up any surviving builder/coder descendants too.
            kill_session(process.pid)
            if sender is not None:
                sender.join()
            else:
                process.stdin.close()
            stdout.seek(0)
            stderr.seek(0)
            output, error = stdout.read(output_bytes + 1), stderr.read(output_bytes + 1)
            if len(output) > output_bytes or len(error) > output_bytes:
                raise ValueError("Isolated process exceeded output limits")
            return {"returncode": process.returncode, "stdout": output, "stderr": error,
                    "wall_seconds": usage["finished"] - started, "timed_out": timed_out,
                    "execution_seconds": None if ready_at is None else usage["finished"] - ready_at,
                    "setup_seconds": None if ready_at is None else ready_at - started,
                    "peak_rss_kib": usage["peak_rss_kib"],
                    "user_seconds": usage["user_seconds"], "system_seconds": usage["system_seconds"],
                    "isolation": "landlock-abi6+-libseccomp", "mode": mode}
