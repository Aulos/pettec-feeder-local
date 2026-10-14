"""Keep all translations complete and interpolation-safe."""

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1] / "custom_components/pettec_local"


@pytest.mark.parametrize("language", ["pl", "de", "fr", "uk"])
def test_translations_have_all_english_keys_and_placeholders(language):
    def flatten(value, prefix=""):
        return {
            path: text
            for key, item in value.items()
            for path, text in (
                flatten(item, f"{prefix}{key}.").items()
                if isinstance(item, dict)
                else [(f"{prefix}{key}", item)]
            )
        }

    english = json.loads((ROOT / "strings.json").read_text())
    assert english == json.loads((ROOT / "translations/en.json").read_text())
    en = flatten(english)
    pl = flatten(json.loads((ROOT / f"translations/{language}.json").read_text()))
    assert en.keys() == pl.keys()
    for key in en:
        assert pl[key].strip(), key
        assert set(re.findall(r"\{\w+\}", en[key])) == set(
            re.findall(r"\{\w+\}", pl[key])
        ), key
