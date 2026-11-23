"""23.11: Vosk не открывал модель из «Проекты» — «Failed to create a model»."""
import os
import sys

import pytest

from core.ascii_path import ascii_model_dir


def test_ascii_path_is_returned_as_is(tmp_path):
    model = tmp_path / "model"
    model.mkdir()
    assert ascii_model_dir(model, base=tmp_path / "links") == model


@pytest.mark.skipif(sys.platform != "win32", reason="junction только на Windows")
def test_cyrillic_path_gets_ascii_link_to_same_files(tmp_path):
    model = tmp_path / "Проекты" / "vosk-small-ru"
    (model / "am").mkdir(parents=True)
    (model / "am" / "final.mdl").write_bytes(b"x")
    result = ascii_model_dir(model, base=tmp_path / "links")
    assert str(result).isascii() and result != model
    assert (result / "am" / "final.mdl").read_bytes() == b"x"
    # повторный вызов берёт готовую ссылку
    assert ascii_model_dir(model, base=tmp_path / "links") == result


@pytest.mark.skipif(sys.platform != "win32", reason="Windows")
def test_real_vosk_model_loads_from_cyrillic_project_path():
    """Сам сбой: модель из папки проекта должна открываться."""
    vosk = pytest.importorskip("vosk")
    from core.wake_vosk import find_model_dir
    model_dir = find_model_dir()
    if model_dir is None or str(model_dir).isascii():
        pytest.skip("модель не найдена или путь уже ASCII")
    vosk.SetLogLevel(-1)
    from core.wake_vosk import _vosk_model
    assert _vosk_model(model_dir.resolve()) is not None
