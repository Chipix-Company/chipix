from pathlib import Path
import sys


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from services.mental_model.builder import _extract_requirements_from_spec


def test_requirement_extraction_ignores_pdf_table_of_contents_rows():
    spec_text = """
1 Technical Requirements ........................................................................................................................ 3
2 Project Management Needs ................................................................................................................. 5
3 Technical Requirements ........................................................................................................................ 6

The bridge shall latch AHB address and data signals when valid transfers occur.
On reset, Hreadyout must be deasserted until the bridge reaches a legal idle state.
"""

    requirements = _extract_requirements_from_spec(spec_text)
    texts = [req.text for req in requirements]

    assert "Technical Requirements" not in " ".join(texts)
    assert "Project Management Needs" not in " ".join(texts)
    assert any("shall latch AHB address" in text for text in texts)
    assert any("Hreadyout must be deasserted" in text for text in texts)
