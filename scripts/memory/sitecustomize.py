"""验收进程通过显式 PYTHONPATH 启用实际请求观察，不修改 Provider。"""

import os
import sys
import threading

from observed_server import observe

if os.environ.get("MEMORY_REQUEST_EVIDENCE"):
    sys.setprofile(observe)
    threading.setprofile(observe)
