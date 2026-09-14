"""Regression coverage for bundled optional-font provenance and notices.

The check intentionally uses only the standard library: packaging verification
must not depend on FontTools being installed in an end-user build environment.
"""

import hashlib
import json
from pathlib import Path


FONT_DIR = Path(__file__).resolve().parents[1] / "frontend" / "fonts"
MANIFEST_PATH = FONT_DIR / "provenance.json"

EXPECTED_SOURCE_STATUS = {
    "seti-original": "supported",
    "extra-glyphs-hack": "supported",
    "devicons": "supported",
    "powerline-symbols": "supported",
    "powerline-extra": "supported",
    "pomicons": "supported",
    "font-awesome": "supported",
    "font-awesome-extension": "supported",
    "iec-power-symbols": "blocked",
    "material-design-icons": "supported",
    "weather-icons": "supported",
    "font-logos": "supported",
    "octicons": "supported",
    "codicons": "supported",
}

EXPECTED_FONT_HASHES = {
    "FiraCodeNerdFontMono-Regular.ttf": (
        "25e08bcd4ce0273c388458fc07b01fa2216fc7e867d37f997482803312764e5d"
    ),
    "JetBrainsMonoNerdFontMono-Regular.ttf": (
        "9e4dad8c34fb31045d53790a936a0afc3aae3fb830e874faadf3670662b04853"
    ),
}


def _manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _notice_path(name: str) -> Path:
    path = Path(name)
    assert not path.is_absolute(), f"notice path must be relative: {name!r}"
    assert ".." not in path.parts, (
        f"notice path must not escape font directory: {name!r}"
    )
    resolved = (FONT_DIR / path).resolve()
    assert resolved.parent == FONT_DIR.resolve(), (
        f"notice path must be a direct font file: {name!r}"
    )
    return resolved


def test_font_notice_manifest_has_a_disposition_for_each_patched_glyph_source():
    manifest = _manifest()

    assert manifest["schema"] == 2
    sources = {source["id"]: source for source in manifest["glyph_sources"]}
    assert {source_id: source["status"] for source_id, source in sources.items()} == (
        EXPECTED_SOURCE_STATUS
    )

    for source_id, source in sources.items():
        assert source["input"], f"{source_id} must name the Nerd Fonts input"
        assert source["ranges"], f"{source_id} must record its patched glyph range"
        if source["status"] == "supported":
            assert source["license"], f"{source_id} must state its cited license"
            assert source["license_source"].startswith("https://"), (
                f"{source_id} must cite an immutable HTTPS license source"
            )
            assert len(source["license_sha256"]) == 64, (
                f"{source_id} must pin its inspected license bytes"
            )
            assert len(source["notice_sha256"]) == 64, (
                f"{source_id} must pin its bundled license or notice bytes"
            )
            assert source["notice_files"], f"{source_id} must name its bundled notice"
        else:
            assert source["status"] == "blocked"
            assert source["blocker"]
            assert source["evidence"]


def test_font_notice_manifest_paths_and_text_are_present_and_not_placeholder_notices():
    manifest = _manifest()
    listed_notices = set(manifest["notices"])

    for source in manifest["glyph_sources"]:
        for notice in source["notice_files"]:
            assert notice in listed_notices, (
                f"{source['id']} references unlisted notice {notice!r}"
            )
        if source["status"] == "supported":
            bundled_license_or_notice = _notice_path(source["notice_files"][0])
            assert (
                hashlib.sha256(bundled_license_or_notice.read_bytes()).hexdigest()
                == (source["notice_sha256"])
            )

    for notice in listed_notices:
        content = _notice_path(notice).read_text(encoding="utf-8-sig")
        assert content.strip(), f"{notice} must not be empty"
        assert "<dates>" not in content
        assert "<Reserved Font Name>" not in content

    assert (
        "Third-party glyph notices"
        in _notice_path("THIRD-PARTY-NOTICES.txt").read_text()
    )
    assert "Apache License" in _notice_path("Apache-2.0-LICENSE.txt").read_text()
    assert "Creative Commons Attribution 4.0" in _notice_path(
        "Codicons-CC-BY-4.0.txt"
    ).read_text(encoding="utf-8-sig")
    assert (
        "SIL Open Font License" in _notice_path("Weather-Icons-OFL-1.1.txt").read_text()
    )


def test_optional_font_binaries_remain_the_pinned_nerd_fonts_outputs():
    manifest = _manifest()
    recorded_hashes = {font["file"]: font["ttf_sha256"] for font in manifest["fonts"]}

    assert recorded_hashes == EXPECTED_FONT_HASHES
    for filename, expected_hash in EXPECTED_FONT_HASHES.items():
        assert (
            hashlib.sha256((FONT_DIR / filename).read_bytes()).hexdigest()
            == expected_hash
        )
