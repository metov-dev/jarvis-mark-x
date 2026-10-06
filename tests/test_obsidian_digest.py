import os
from actions.obsidian import recent_notes_digest
import main as jarvis_main


def test_нет_vault(tmp_path, monkeypatch):
    non_existent = tmp_path / "does_not_exist"
    monkeypatch.setattr("actions.obsidian._config", lambda: {"vault_path": str(non_existent)})
    assert recent_notes_digest() == ""


def test_пустой_vault(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    monkeypatch.setattr("actions.obsidian._config", lambda: {"vault_path": str(vault)})
    assert recent_notes_digest() == ""


def test_порядок_по_дате_изменения(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    monkeypatch.setattr("actions.obsidian._config", lambda: {"vault_path": str(vault)})

    # Создаём 3 заметки с разным временем изменения
    n1 = vault / "Первая.md"
    n1.write_text("Старая заметка\nТекст старой", encoding="utf-8")
    os.utime(n1, (1000, 1000))

    n2 = vault / "Вторая.md"
    n2.write_text("Средняя заметка\nТекст средней", encoding="utf-8")
    os.utime(n2, (2000, 2000))

    n3 = vault / "Третья.md"
    n3.write_text("Свежая заметка\nТекст свежей", encoding="utf-8")
    os.utime(n3, (3000, 3000))

    digest = recent_notes_digest(limit=3)
    assert "[ПОСЛЕДНИЕ ЗАМЕТКИ В OBSIDIAN]" in digest
    # Свежая должна быть раньше старых
    pos3 = digest.find("Третья")
    pos2 = digest.find("Вторая")
    pos1 = digest.find("Первая")
    assert pos3 != -1 and pos2 != -1 and pos1 != -1
    assert pos3 < pos2 < pos1


def test_включая_daily(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    daily_dir = vault / "Daily"
    daily_dir.mkdir(parents=True)
    monkeypatch.setattr("actions.obsidian._config", lambda: {"vault_path": str(vault)})

    daily = daily_dir / "2026-10-06.md"
    daily.write_text("# 2026-10-06\n\n- 18:00 Встреча с инвестором", encoding="utf-8")

    digest = recent_notes_digest(limit=5)
    assert "2026-10-06" in digest
    assert "Встреча с инвестором" in digest


def test_лимит_символов_без_обрыва_посреди_слова(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    monkeypatch.setattr("actions.obsidian._config", lambda: {"vault_path": str(vault)})

    n = vault / "Длинная.md"
    # Текст с известными словами
    n.write_text("СловоПервое СловоВторое СловоТретье СловоЧетвертое СловоПятое", encoding="utf-8")

    limit = 65
    digest = recent_notes_digest(limit=5, max_chars=limit)
    assert len(digest) <= limit
    assert digest.endswith("…")
    # Проверяем, что обрыв произошёл по границе слова, а не посреди него
    content_part = digest.replace("[ПОСЛЕДНИЕ ЗАМЕТКИ В OBSIDIAN]\n", "")
    assert not content_part.endswith("СловоЧ…")
    assert not content_part.endswith("СловоП…")


def test_выключатель(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "Заметка.md").write_text("Текст заметки", encoding="utf-8")
    monkeypatch.setattr("actions.obsidian._config", lambda: {"vault_path": str(vault)})

    # Включено по умолчанию
    assert recent_notes_digest() != ""

    # Выключатель через JARVIS_OBSIDIAN_DIGEST=0
    monkeypatch.setenv("JARVIS_OBSIDIAN_DIGEST", "0")
    assert recent_notes_digest() == ""


def test_сборка_промпта_в_main(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "Тестовая.md").write_text("Важный факт из базы", encoding="utf-8")
    monkeypatch.setattr("actions.obsidian._config", lambda: {"vault_path": str(vault)})

    class _DummyUI:
        def write_log(self, *a, **k): pass

    jarvis = jarvis_main.Jarvis(_DummyUI())
    config = jarvis._build_config()
    assert "[ПОСЛЕДНИЕ ЗАМЕТКИ В OBSIDIAN]" in config.system_instruction
    assert "Тестовая" in config.system_instruction
    assert "Важный факт из базы" in config.system_instruction
