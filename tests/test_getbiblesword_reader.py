import zipfile

import pytest

from getbiblesword_reader import GetBibleSwordError, materialize_sword_root


def make_zip(path, files):
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)


def test_materializes_a_deterministic_sword_root(tmp_path):
    first = tmp_path / "A.zip"
    second = tmp_path / "B.zip"
    make_zip(first, {"mods.d/a.conf": "[A]\n", "modules/a.dat": "A"})
    make_zip(second, {"mods.d/b.conf": "[B]\n", "modules/b.dat": "B"})
    root = materialize_sword_root([str(second), str(first)], str(tmp_path / "root"))
    assert (root / "mods.d" / "a.conf").read_text() == "[A]\n"
    assert (root / "modules" / "b.dat").read_text() == "B"


def test_rejects_zip_slip(tmp_path):
    archive = tmp_path / "bad.zip"
    make_zip(archive, {"../outside": "no", "mods.d/a.conf": "[A]\n"})
    with pytest.raises(GetBibleSwordError, match="unsafe ZIP member"):
        materialize_sword_root([str(archive)], str(tmp_path / "root"))


def test_rejects_conflicting_module_paths(tmp_path):
    first = tmp_path / "A.zip"
    second = tmp_path / "B.zip"
    make_zip(first, {"mods.d/shared.conf": "first"})
    make_zip(second, {"mods.d/shared.conf": "second"})
    with pytest.raises(GetBibleSwordError, match="conflicting path"):
        materialize_sword_root([str(first), str(second)], str(tmp_path / "root"))
