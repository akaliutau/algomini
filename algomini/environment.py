"""Secret-safe preflight and reproducibility records for local Linux runs."""

from datetime import datetime, timezone
import importlib.metadata as metadata
import json
import os
from pathlib import Path
import platform
import shutil
import sqlite3
import subprocess
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .common import digest, download, sha256, worker_environment, write_json


PACKAGES = ("python-dotenv", "duckdb", "numpy", "scipy", "scikit-learn", "pytest")
TOOLS = {"rustc": ["rustc", "--version"], "cargo": ["cargo", "--version"],
         "gzip": ["gzip", "--version"], "xz": ["xz", "--version"],
         "zstd": ["zstd", "--version"], "file": ["file", "--version"],
         "xxd": ["xxd", "-v"], "strings": ["strings", "--version"],
         "prlimit": ["prlimit", "--version"], "time": ["/usr/bin/time", "--version"]}


def command_output(argv):
    completed = subprocess.run(argv, capture_output=True, text=True, timeout=30,
                               env=worker_environment(), check=True)
    return (completed.stdout + completed.stderr).strip()


def bootstrap_muse(root, cfg):
    release = json.loads((root / "repro/muse-release.json").read_text())
    machine = {"x86_64": "x86", "aarch64": "aarch64"}.get(platform.machine())
    if platform.system() != "Linux" or machine is None:
        raise ValueError("The local bootstrap currently supports Linux x86_64 and aarch64")
    artifact = release["artifacts"][f"{machine}_linux"]
    target = root / cfg["coder"]["executable"]
    if target.name != f"muse-bin-{release['version']}":
        raise ValueError("Configured Muse executable does not match the pinned release")
    download(artifact["url"], target, artifact["checksum"], artifact["size"])
    target.chmod(0o755)
    return {"version": command_output([str(target), "--version"]), "sha256": sha256(target),
            "path": target.relative_to(root).as_posix()}


def check_api(root, cfg):
    from dotenv import dotenv_values

    coder = cfg["coder"]
    key = os.environ.get(coder["api_key_env"]) or dotenv_values(root / ".env", interpolate=False).get(coder["api_key_env"])
    if not key or not key.strip():
        return {"ok": False, "reason": "API key is missing", "inference_calls": 0}
    # Authenticated requests must never follow a redirect to another host.
    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    if coder["api_base"] != "https://api.meta.ai/v1":
        raise ValueError("Stage 0 Meta credential checks require the official API origin")
    request = Request(coder["api_base"] + "/models", headers={"Authorization": f"Bearer {key.strip()}"})
    try:
        with build_opener(NoRedirect()).open(request, timeout=30) as response:
            body = response.read(2 * 1024**2 + 1)
            if len(body) > 2 * 1024**2:
                raise ValueError("Model catalog exceeds the response cap")
            catalog = json.loads(body)
        available = any(model.get("id") == coder["model"] for model in catalog.get("data", []))
        return {"ok": available, "http_status": 200, "selected_model": coder["model"],
                "selected_model_available": available, "inference_calls": 0,
                "operation": "GET /v1/models"}
    except HTTPError as error:
        # Never expose headers, response bodies, key fingerprints, or raw exception text.
        return {"ok": False, "http_status": error.code, "reason": "Model catalog request was rejected", "inference_calls": 0}
    except (URLError, TimeoutError, json.JSONDecodeError):
        return {"ok": False, "reason": "Model catalog could not be read", "inference_calls": 0}


def doctor(root, cfg, api=False):
    from dotenv import dotenv_values

    packages = {}
    for name in PACKAGES:
        try:
            packages[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            packages[name] = None
    tools = {name: shutil.which(argv[0]) is not None for name, argv in TOOLS.items()}
    executable = root / cfg["coder"]["executable"]
    tools["coder"] = executable.is_file() and os.access(executable, os.X_OK)
    cli_contract = False
    if tools["coder"]:
        help_text = command_output([str(executable), "exec", "--help"])
        flags = [arg for arg in cfg["coder"]["command"] if arg.startswith("--")]
        cli_contract = all(flag in help_text for flag in flags)
    key_name = cfg["coder"]["api_key_env"]
    key_present = bool(os.environ.get(key_name) or dotenv_values(root / ".env", interpolate=False).get(key_name))
    ignored = subprocess.run(["git", "check-ignore", "--quiet", ".env"], cwd=root, capture_output=True).returncode == 0
    result = {"schema_version": 1, "python": platform.python_version(),
              "platform": platform.system(), "packages": packages, "tools": tools,
              "coder_cli_contract": cli_contract, "api_key_present": key_present, "env_file_gitignored": ignored,
              "model_inference_enabled": cfg["budget"]["generation_enabled"],
              "provider_spending_cap_verified": cfg["budget"]["provider_spending_cap_verified"],
              "candidate_containment": "Stage 2 required; Stage 0 runs trusted local tools only"}
    if api:
        result["api"] = check_api(root, cfg)
    result["ok"] = (sys.version_info >= (3, 12) and platform.system() == "Linux" and all(packages.values())
                    and all(tools.values()) and cli_contract and key_present and ignored
                    and (not api or result["api"]["ok"]))
    write_json(root / cfg["report_dir"] / "preflight.json", result)
    return result


def snapshot(root, cfg, *, destination="repro/environment.json", write_locks=True):
    tools = {}
    for name, argv in {**TOOLS, "muse": [str(root / cfg["coder"]["executable"]), "--version"]}.items():
        executable = shutil.which(argv[0])
        tools[name] = {"version": command_output(argv).splitlines()[0],
                       "sha256": sha256(Path(executable).resolve())} if executable else {"missing": True}
    packages = {dist.metadata["Name"]: dist.version for dist in metadata.distributions() if dist.metadata["Name"]}
    cpu = next((line.partition(":")[2].strip() for line in Path("/proc/cpuinfo").read_text().splitlines()
                if line.startswith("model name")), platform.machine())
    result = {"schema_version": 1, "captured_at": datetime.now(timezone.utc).isoformat(),
              "python": {"version": platform.python_version(), "implementation": platform.python_implementation(),
                         "sqlite": sqlite3.sqlite_version},
              "os_release": platform.freedesktop_os_release(), "kernel": platform.release(),
              "machine": platform.machine(), "libc": list(platform.libc_ver()),
              "hardware": {"cpu": cpu, "logical_cpus": os.cpu_count(),
                           "affinity_cpus": len(os.sched_getaffinity(0))},
              "packages": dict(sorted(packages.items())), "tools": tools,
              "configuration_sha256": digest(json.dumps(cfg, sort_keys=True).encode()),
              "source_sha256": {path.relative_to(root).as_posix(): sha256(path)
                                for path in sorted((root / "algomini").glob("*.py"))},
              "git_head": command_output(["git", "-C", str(root), "rev-parse", "HEAD"])}
    write_json(root / destination, result)
    if not write_locks:
        return result
    lock = "# Exact Python distributions captured by python -m algomini versions.\n"
    lock += "\n".join(f"{name}=={version}" for name, version in sorted(packages.items())
                      if name.lower() not in {"pip", "setuptools", "wheel"}) + "\n"
    (root / "requirements.lock").write_text(lock)
    if shutil.which("conda"):
        explicit = command_output(["conda", "list", "--prefix", sys.prefix, "--explicit", "--md5"])
        for line in explicit.splitlines():
            if "://" in line:
                url = urlsplit(line)
                if url.username or url.password or url.query or url.hostname not in {"repo.anaconda.com", "conda.anaconda.org"} or "/t/" in url.path:
                    raise ValueError("Refusing to export a potentially credential-bearing Conda channel")
        (root / "repro/conda-linux-64.lock").write_text(explicit + "\n")
    if shutil.which("dpkg-query"):
        packages = ("gzip", "zstd", "file", "xxd", "binutils", "util-linux", "time")
        lines = command_output(["dpkg-query", "-W", "-f", "${Package}\t${Version}\n", *packages]).splitlines()
        (root / "repro/system-packages.lock").write_text("\n".join(line.replace("\t", "=") for line in lines) + "\n")
    return result


def verify_versions(root, cfg):
    expected = json.loads((root / "repro/environment.json").read_text())
    differences = {}
    if platform.python_version() != expected["python"]["version"]:
        differences["python"] = {"expected": expected["python"]["version"], "actual": platform.python_version()}
    for name, version in expected["packages"].items():
        try:
            actual = metadata.version(name)
        except metadata.PackageNotFoundError:
            actual = None
        if actual != version:
            differences[name] = {"expected": version, "actual": actual}
    for name, argv in {**TOOLS, "muse": [str(root / cfg["coder"]["executable"]), "--version"]}.items():
        actual = command_output(argv).splitlines()[0] if shutil.which(argv[0]) else None
        version = expected["tools"][name].get("version")
        if actual != version:
            differences[name] = {"expected": version, "actual": actual}
    return {"ok": not differences, "differences": differences,
            "scope": "Exact Python/package and CLI version strings; hardware and binary hashes are recorded separately"}
