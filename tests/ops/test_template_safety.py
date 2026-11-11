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


def test_copy_buttons_put_plain_text_on_the_clipboard():
    # owner 2026-11-11: every Notes block copies as Markdown text; the clipboard never receives HTML
    js = TEMPLATE.read_text()
    assert "navigator.clipboard.writeText(text)" in js and "clipboard.write(" not in js and "text/html" not in js
    for label in ('"Copy everything"', '"Copy day"', '"Copy report"', '"Copy brain"', '"Insert template"'):
        assert label in js, label
    for box in ('"Luna review"', '"Your notes"', '"Timeline (ET)"', '"What the agent knew"', '"X catalyst posts (24h)"'):
        assert f"jnHead({box}" in js, box


def test_notes_links_on_the_fade_tab_are_routed_from_page_load():
    # owner 2026-11-11: the fade cards' "Notes & Luna review" link did nothing, because the #notes hash listener was
    # only registered when the Notes tab first opened. The router must be set up in boot(), not in jnBoot().
    js = TEMPLATE.read_text()
    boot = js[js.index("function boot() {"):js.index("if (window.LightweightCharts && DATA) boot();")]
    jn = js[js.index("async function jnBoot() {"):js.index("function showTab(name) {")]
    assert 'addEventListener("hashchange"' in boot and 'showTab("notes")' in boot
    assert 'addEventListener("hashchange"' not in jn
    assert js.count('addEventListener("hashchange"') == 1
    assert 'n.href = "#notes/" + t.key' in js
