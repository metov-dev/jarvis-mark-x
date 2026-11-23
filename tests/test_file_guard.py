"""Копирование папки не должно выносить защищённые файлы изнутри (ревью безопасности 24.11)."""
from actions.file_controller import _transfer_blocked


def test_directory_with_secret_inside_is_blocked(tmp_path):
    src = tmp_path / "docs"
    src.mkdir()
    (src / "note.txt").write_text("ok")
    (src / ".env").write_text("TOKEN=1")
    assert _transfer_blocked(str(src), str(tmp_path / "copy"))


def test_plain_directory_and_file_are_allowed(tmp_path):
    src = tmp_path / "docs"
    src.mkdir()
    (src / "note.txt").write_text("ok")
    assert not _transfer_blocked(str(src), str(tmp_path / "copy"))
    assert not _transfer_blocked(str(src / "note.txt"), str(tmp_path))


def test_destination_directory_final_path_is_checked(tmp_path):
    src = tmp_path / "id_rsa.txt"
    src.write_text("x")
    out = tmp_path / ".ssh"
    out.mkdir()
    assert _transfer_blocked(str(src), str(out))
