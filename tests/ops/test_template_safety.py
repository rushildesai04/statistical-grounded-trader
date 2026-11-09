from __future__ import annotations

import re
from pathlib import Path

TEMPLATE = Path(__file__).resolve().parents[2] / "src" / "sgt" / "ops" / "templates" / "trade_charts.html"


def test_only_sanitized_markdown_reaches_innerhtml():
    # adversarial F-2: the page's sanitisation is browser-only; pin it statically. Every innerHTML sink must be the
    # Markdown preview, fed through DOMPurify.sanitize; all other untrusted text goes through el() (textContent).
    js = TEMPLATE.read_text()
    sinks = [m.start() for m in re.finditer(r"\.innerHTML\s*=", js)]
    assert len(sinks) == 1, "a new innerHTML sink appeared"
    line = js[js.rfind("\n", 0, sinks[0]):js.find("\n", sinks[0])]
    assert "DOMPurify.sanitize(" in line and "marked.parse(" in line
    assert "insertAdjacentHTML" not in js and "document.write" not in js and "outerHTML" not in js
    assert re.search(r"const el = \(tag, cls, text\) => \{[^}]*e\.textContent = text", js)


def test_markdown_libraries_are_pinned():
    js = TEMPLATE.read_text()
    assert "unpkg.com/marked@12.0.2/" in js and "unpkg.com/dompurify@3.1.6/" in js
    assert js.count('"sha384-') >= 2 and 'crossOrigin = "anonymous"' in js            # F-7: SRI on both
