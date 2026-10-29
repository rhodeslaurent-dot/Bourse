"""29/10/2026: Windows PowerShell 5.1 reads a .ps1 without a byte-order mark as Windows-1252 — the « — » of a
message became « â€” », whose last byte is a closing double quote for PowerShell, and scripts\\nuit.ps1 did not
parse. Every script with a non-ASCII character starts with the UTF-8 BOM."""

from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


def test_every_powershell_script_with_accents_has_a_utf8_bom():
    bad = [
        p.name
        for p in sorted(SCRIPTS.glob("*.ps1"))
        if any(c > 127 for c in p.read_bytes()) and not p.read_bytes().startswith(b"\xef\xbb\xbf")
    ]
    assert bad == []
