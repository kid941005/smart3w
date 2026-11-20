"""document_extract CLI 回归测试（无网络依赖）。"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import document_extract  # noqa: E402

FIXTURE_PDF = Path(__file__).resolve().parent / "fixtures" / "sample.pdf"


def test_write_failure_returns_one(tmp_path, capsys):
    # 目标是已存在的目录：os.replace 会抛 OSError，CLI 必须返回 1 而不是 traceback
    output = tmp_path / "outdir"
    output.mkdir()

    rc = document_extract.main([str(FIXTURE_PDF), str(output)])

    assert rc == 1
    assert "failed to write output" in capsys.readouterr().err
