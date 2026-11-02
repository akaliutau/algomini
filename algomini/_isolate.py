"""Linux Landlock/seccomp launcher. Run only through execution.run_isolated.

This is a small host execution boundary, not a container: it restricts file
contents, network syscalls, process inspection, writes, and resource use.
"""

import ctypes
import ctypes.util
import errno
import json
import os
from pathlib import Path
import resource
import sys


class Ruleset(ctypes.Structure):
    _fields_ = [("fs", ctypes.c_uint64), ("net", ctypes.c_uint64), ("scoped", ctypes.c_uint64)]


class PathRule(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("access", ctypes.c_uint64), ("parent_fd", ctypes.c_int)]


def enforce(job):
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    if os.uname().machine not in ("x86_64", "aarch64"):
        raise RuntimeError("Unsupported Landlock syscall architecture")
    # Linux uses these syscall numbers on both supported architectures.
    abi = libc.syscall(444, 0, 0, 1)
    if abi < 6:
        raise RuntimeError("Landlock ABI 6 or newer is required; no unrestricted fallback")
    if libc.prctl(38, 1, 0, 0, 0) != 0:  # PR_SET_NO_NEW_PRIVS
        raise OSError(ctypes.get_errno(), "no_new_privs failed")
    # ABI 5 filesystem rights through IOCTL_DEV; ABI 6 process/signal scopes.
    handled = (1 << 16) - 1
    attributes = Ruleset(handled, 0 if job["network"] else 3, 3)
    ruleset = libc.syscall(444, ctypes.byref(attributes), ctypes.sizeof(attributes), 0)
    if ruleset < 0:
        raise OSError(ctypes.get_errno(), "Landlock ruleset creation failed")
    read = (1 << 0) | (1 << 2) | (1 << 3)
    write = sum(1 << bit for bit in (1, 2, 3, 4, 5, 7, 8, 12, 13, 14))
    try:
        # Native filesystem APIs open the root directory before traversing a
        # permitted path. Directory metadata is visible; file contents are not.
        for access, paths in ((read, job["ro"]), (write, job["rw"]), (1 << 3, ["/"])):
            for value in paths:
                path = Path(value).resolve()
                if not path.exists():
                    continue
                rights = access if path.is_dir() else access & ((1 << 0) | (1 << 1) | (1 << 2) | (1 << 14) | (1 << 15))
                descriptor = os.open(path, os.O_PATH | os.O_CLOEXEC)
                try:
                    rule = PathRule(rights, descriptor)
                    if libc.syscall(445, ruleset, 1, ctypes.byref(rule), 0) < 0:
                        raise OSError(ctypes.get_errno(), "Landlock path rule failed")
                finally:
                    os.close(descriptor)
        if libc.syscall(446, ruleset, 0) < 0:
            raise OSError(ctypes.get_errno(), "Landlock restriction failed")
    finally:
        os.close(ruleset)

    seccomp = ctypes.CDLL(ctypes.util.find_library("seccomp") or "libseccomp.so.2", use_errno=True)
    seccomp.seccomp_init.argtypes = [ctypes.c_uint32]
    seccomp.seccomp_init.restype = ctypes.c_void_p
    seccomp.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    seccomp.seccomp_syscall_resolve_name.restype = ctypes.c_int
    seccomp.seccomp_rule_add.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, ctypes.c_uint]
    seccomp.seccomp_load.argtypes = [ctypes.c_void_p]
    seccomp.seccomp_release.argtypes = [ctypes.c_void_p]
    context = seccomp.seccomp_init(0x7fff0000)  # SCMP_ACT_ALLOW; libseccomp checks ABI/architecture.
    if not context:
        raise RuntimeError("seccomp initialization failed")
    denied = ["ptrace", "process_vm_readv", "process_vm_writev", "pidfd_getfd", "pidfd_send_signal",
              "bpf", "perf_event_open", "mount", "umount2", "pivot_root", "setns", "unshare",
              "open_by_handle_at", "userfaultfd", "io_uring_setup", "kexec_load", "init_module",
              "finit_module", "delete_module", "keyctl", "add_key", "request_key", "reboot",
              "setsid"]
    if not job["network"]:
        denied += ["socket", "socketcall", "connect", "bind", "listen", "accept", "accept4"]
    if job["mode"] == "worker":
        denied += ["clone", "clone3", "fork", "vfork", "setpgid", "socketpair"]
    try:
        for name in denied:
            number = seccomp.seccomp_syscall_resolve_name(name.encode())
            if number >= 0 and seccomp.seccomp_rule_add(context, 0x00050000 | errno.EPERM, number, 0) != 0:
                raise RuntimeError("seccomp rule installation failed")
        if seccomp.seccomp_load(context) != 0:
            raise RuntimeError("seccomp load failed")
    finally:
        seccomp.seccomp_release(context)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_AS, (job["memory_bytes"], job["memory_bytes"]))
    resource.setrlimit(resource.RLIMIT_CPU, (job["cpu_seconds"], job["cpu_seconds"]))
    resource.setrlimit(resource.RLIMIT_FSIZE, (job["output_bytes"], job["output_bytes"]))
    resource.setrlimit(resource.RLIMIT_NOFILE, (256, 256))


if __name__ == "__main__":
    try:
        job = json.loads(Path(sys.argv[1]).read_text())
        os.chdir(job["cwd"])
        enforce(job)
        os.execvpe(job["argv"][0], job["argv"], dict(os.environ))
    except Exception as error:
        print(f"isolation: {type(error).__name__}: {error}", file=sys.stderr)
        raise SystemExit(125)
