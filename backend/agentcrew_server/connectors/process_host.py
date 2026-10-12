"""stdio服务的进程组宿主，服务退出时清理全部同组子进程。"""

import os
import signal
import subprocess
import sys


def main():
    command = sys.argv[1:]
    if not command:
        raise ValueError("stdio宿主缺少启动命令")
    environment = {name: os.environ[name] for name in ("PATH", "HOME", "LANG", "TZ", "TERM") if name in os.environ}
    child = subprocess.Popen(command, env=environment)
    try:
        child.wait()
    finally:
        os.killpg(os.getpgrp(), signal.SIGKILL)


if __name__ == "__main__":
    main()
