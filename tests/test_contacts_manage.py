"""28.11: нельзя было удалить все контакты, а «Подтянуть из Telegram» добавлял всех подряд."""
import main as jarvis_main
from core import contacts as CT

TG = [(1, "Азиз Каримов", "@aziz"), (2, "Мама", "+77010000000"), (3, "Азиза", "@aziza")]


def _api(tmp_path, monkeypatch):
    book = CT.Book(tmp_path / "c.json")
    api = CT.Contacts(book, CT.Me(str(tmp_path / "s")))
    monkeypatch.setattr(api, "telegram_contacts", lambda: list(TG))
    monkeypatch.setattr(CT, "contacts", lambda: api)
    return api


def test_delete_all_needs_confirmation_and_clears(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    api.add_telegram_users(TG)
    assert jarvis_main._is_destructive("contacts", {"action": "delete_all"})
    assert jarvis_main._confirm_label("contacts", {"action": "delete_all"}) == "Удалить все контакты?"
    assert "Удалил все контакты (3)" in CT.contacts_tool({"action": "delete_all"})
    assert api.book.contacts == []


def test_import_only_selected(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    assert "Добавил 1 из 1" in api.import_from_telegram(only={2})
    assert [c.name for c in api.book.contacts] == ["Мама"]


def test_voice_add_one_from_telegram(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    assert "Добавил «Мама»" in CT.contacts_tool({"action": "import", "name": "маму"})
    assert "уже в контактах" in CT.contacts_tool({"action": "import", "name": "мама"})
    assert "нет «Петя»" in CT.contacts_tool({"action": "import", "name": "Петя"})
    assert "Кого добавить" in CT.contacts_tool({"action": "import", "name": ""})


def test_latin_contact_found_by_cyrillic_name(tmp_path, monkeypatch):
    """28.11 01:33: контакт «Sardorbek», просили «Сардорбек» — «нет в контактах»."""
    api = _api(tmp_path, monkeypatch)
    api.book.upsert(CT.Contact(name="Sardorbek", telegram="@sardor"))
    assert [c.name for c in api.book.find("Сардорбек")] == ["Sardorbek"]
    assert [c.name for c in api.book.find("Сардорбеку")] == ["Sardorbek"]
