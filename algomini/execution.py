"""Bounded local process supervision for builds, coding, and codec workers."""

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
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
    if mode not in ("worker", "build", "coder") or (network and mode != "coder"):
        raise ValueError("Invalid isolation mode/network combination")
    # Open logs before restriction; they are the only inherited writable output descriptors.
    with tempfile.TemporaryDirectory(prefix="algomini-exec-") as temporary:
        directory = Path(temporary)
        ticket = directory / "job.json"
        job = {"argv": list(map(str, argv)), "cwd": str(Path(cwd).resolve()),
               "ro": SYSTEM_READ + list(map(str, ro)), "rw": ["/dev/null"] + list(map(str, rw)),
               "network": network, "mode": mode, "memory_bytes": memory_mib * 1024**2,
               "cpu_seconds": max(1, int(timeout) + 1), "output_bytes": output_bytes}
        ticket.write_text(json.dumps(job))
        launcher = Path(__file__).with_name("_isolate.py")
        with (directory / "stdout").open("w+b") as stdout, (directory / "stderr").open("w+b") as stderr:
            started = time.perf_counter()
            process = subprocess.Popen([sys.executable, "-I", str(launcher), str(ticket)],
                                       stdin=subprocess.PIPE, stdout=stdout, stderr=stderr,
                                       env=worker_environment() if env is None else env,
                                       start_new_session=True, close_fds=True)
            timed_out = False
            try:
                process.communicate(data, timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                kill_session(process.pid)
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.communicate()
            stdout.seek(0)
            stderr.seek(0)
            output, error = stdout.read(output_bytes + 1), stderr.read(output_bytes + 1)
            if len(output) > output_bytes or len(error) > output_bytes:
                raise ValueError("Isolated process exceeded output limits")
            return {"returncode": process.returncode, "stdout": output, "stderr": error,
                    "wall_seconds": time.perf_counter() - started, "timed_out": timed_out,
                    "isolation": "landlock-abi6+-libseccomp", "mode": mode}
