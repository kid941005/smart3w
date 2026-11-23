"""测试环境默认使用临时任务状态文件，避免污染开发机 ~/.smart3w。"""

import os
import tempfile
from pathlib import Path

os.environ.setdefault(
    "SMART3W_JOB_STATE",
    str(Path(tempfile.mkdtemp(prefix="smart3w-jobs-")) / "jobs.json"),
)
