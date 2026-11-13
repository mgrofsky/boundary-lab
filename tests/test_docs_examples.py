"""Run the Python examples in the application guide, so the page cannot drift."""

import re
from pathlib import Path

PAGE = Path(__file__).parent.parent / "docs" / "using-redline-in-an-app.md"
BLOCK = re.compile(r"^```python\n(.*?)^```$", re.DOTALL | re.MULTILINE)


def test_the_examples_in_the_application_guide_run(tmp_path, monkeypatch):
    """The blocks run in page order as one script, in an empty folder."""
    blocks = BLOCK.findall(PAGE.read_text())
    assert len(blocks) >= 8
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("REDLINE_JUDGE_API_KEY", raising=False)
    names = {"__name__": "guide"}
    for number, block in enumerate(blocks, start=1):
        exec(compile(block, f"{PAGE.name} block {number}", "exec"), names)  # noqa: S102
    # The page says what the examples leave behind. Check they really ran.
    assert (tmp_path / "saved_grades.jsonl").read_text().count("\n") == 2
    assert (tmp_path / "scan-17" / "garak_summary.json").exists()
    assert names["summary"]["stopped"] == "cap"


def test_every_code_and_stop_reason_is_on_the_page():
    from redline.garak import STOP_REASONS
    from redline.policy import PROBLEM_CODES

    text = PAGE.read_text()
    missing = [name for name in (*PROBLEM_CODES, *STOP_REASONS) if f"`{name}`" not in text]
    assert missing == []
