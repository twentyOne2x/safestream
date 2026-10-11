import subprocess
from pathlib import Path

HELPER = Path(__file__).resolve().parents[1] / ".build/native-capture"


def test_invalid_capture_arguments_never_request_a_display():
    for args in [[], ["0", "0", "0", "100"], ["0", "0", "100", "-1"],
                 ["private-sentinel", "0", "100", "100"], ["0", "0", "32769", "100"]]:
        result = subprocess.run([str(HELPER), *args], stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, timeout=3)
        assert result.returncode != 0
        assert result.stdout == b""
        assert result.stderr == b"Local display capture failed\n"
