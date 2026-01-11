from __future__ import annotations

from pathlib import Path
from datetime import datetime
import pytest


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def pytest_sessionstart(session: pytest.Session) -> None:
    Path("test-result").mkdir(parents=True, exist_ok=True)


def _get_test_doc(item: pytest.Item) -> str:
    # item.function은 python test function (pytest가 수집한 경우)
    fn = getattr(item, "function", None)
    doc = getattr(fn, "__doc__", None) if fn else None
    if not doc:
        return "(No experiment description. Add a docstring to the test.)"
    return doc.strip()

def _get_captured_output(rep: pytest.TestReport) -> str:
    # pytest가 캡처한 출력은 rep.sections에 들어옵니다.
    # 예: ("Captured stdout call", "...내용...")
    chunks = []
    for name, content in getattr(rep, "sections", []):
        if "Captured stdout" in name or "Captured stderr" in name:
            chunks.append(f"[{name}]\n{content}")
    return "\n".join(chunks).strip()

@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo):
    outcome = yield
    rep: pytest.TestReport = outcome.get_result()
    if rep.when != "call":
        return

    test_file = Path(str(item.fspath)).name if getattr(item, "fspath", None) else "unknown_test_file.py"
    out_path = Path("test-result") / f"{_timestamp()}_{test_file}.txt"

    nodeid = rep.nodeid
    status = "PASS" if rep.passed else "FAIL" if rep.failed else "SKIP"

    desc = _get_test_doc(item)

    details = ""
    if rep.failed and rep.longrepr:
        details = "\n[TRACEBACK]\n" + str(rep.longrepr)

    captured = _get_captured_output(rep)
    if captured:
        captured = "\n[CAPTURED OUTPUT]\n" + captured + "\n"

    block = (
        "============================================================\n"
        f"[{status}] {nodeid}\n"
        "------------------------------------------------------------\n"
        "[EXPERIMENT]\n"
        f"{desc}\n"
        f"{captured}\n"
        f"{details}\n"
    )

    with out_path.open("a", encoding="utf-8") as f:
        f.write(block)
