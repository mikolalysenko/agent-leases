"""Linux worker isolation. Fail closed on unsupported hosts or missing bubblewrap."""

import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys


def command(socket_path, workspace, argv):
    if sys.platform != "linux":
        raise ValueError("sandbox execution requires Linux and bubblewrap; there is no unsandboxed fallback")
    bwrap = shutil.which("bwrap")
    if not bwrap:
        raise ValueError("install bubblewrap before running a worker")
    endpoint = Path(socket_path).resolve(strict=True)
    work = Path(workspace).resolve(strict=True)
    source = Path(__file__).resolve().parent
    if not endpoint.is_socket() or not work.is_dir():
        raise ValueError("expected a gateway socket and a workspace directory")
    # Mounting the authority, its parent, or this package as writable defeats isolation.
    if work == Path("/") or endpoint.is_relative_to(work) or source.is_relative_to(work):
        raise ValueError("workspace must not contain the gateway state or package")
    # A preexisting Unix socket or hard link in the bind mount is another boundary.
    # The operator must keep this directory private and not mutate it during launch.
    for root, dirs, files in os.walk(work):
        for name in dirs + files:
            info = (Path(root) / name).lstat()
            if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
                raise ValueError("workspace may contain only ordinary files and directories")
            if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
                raise ValueError("workspace must not contain hard-linked files")
    if not argv:
        raise ValueError("provide a worker command after --")
    args = [bwrap, "--unshare-all", "--die-with-parent", "--new-session", "--cap-drop", "ALL",
            "--clearenv", "--setenv", "PATH", "/usr/bin:/bin",
            "--setenv", "HOME", "/home/agent", "--setenv", "LANG", "C.UTF-8",
            "--setenv", "PYTHONPATH", "/opt/lease", "--setenv", "PYTHONDONTWRITEBYTECODE", "1",
            "--setenv", "LEASE_SOCKET", "/run/lease.sock"]
    for directory in ("/usr", "/bin", "/sbin", "/lib", "/lib64"):
        if Path(directory).exists():
            args += ["--ro-bind", directory, directory]
    args += ["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--dir", "/home/agent",
             "--dir", "/run", "--ro-bind", str(endpoint), "/run/lease.sock",
             "--ro-bind", str(source), "/opt/lease/agent_leases",
             "--bind", str(work), "/workspace", "--chdir", "/workspace", "--", *argv]
    return args


def run(socket_path, workspace, argv):
    # close_fds prevents inherited network/credential descriptors becoming exits.
    # Detached stdin removes a host-controlled input stream and terminal ioctl surface.
    return subprocess.run(command(socket_path, workspace, argv), env={}, close_fds=True,
                          stdin=subprocess.DEVNULL).returncode
