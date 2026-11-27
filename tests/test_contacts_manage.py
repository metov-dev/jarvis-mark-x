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


def test_message_goes_from_jarvis_account_by_default(tmp_path, monkeypatch):
    """28.11 01:34: владелец просил писать с аккаунта Джарвиса, а не со своего."""
    from core import tg_call
    api = _api(tmp_path, monkeypatch)
    api.book.upsert(CT.Contact(name="Мама", telegram="@mama"))
    monkeypatch.setattr(api, "precheck", lambda *a, **k: (api.book.find("Мама")[0], ""))
    monkeypatch.setattr(CT, "_owner_name", lambda: "Сардорбек")
    used = []
    monkeypatch.setattr(tg_call, "ready", lambda: "")
    monkeypatch.setattr(tg_call, "run_as_jarvis", lambda fn: used.append("jarvis") or (0, False))
    monkeypatch.setattr(api.me, "run", lambda fn, timeout=40: used.append("owner") or (5, False))
    assert "с аккаунта Джарвиса" in api.message("мама", "Задержусь")
    assert "с вашего Telegram" in api.message("мама", "Задержусь", from_me=True)
    assert used == ["jarvis", "owner"]
    monkeypatch.setattr(tg_call, "ready", lambda: "Аккаунт Джарвиса для звонков не подключён")
    assert "не подключён" in api.message("мама", "Задержусь")


def test_call_to_owner_contact_goes_via_jarvis_account(tmp_path, monkeypatch):
    """28.11 01:36: контакт «Sardorbek» — сам владелец; звонок с его же Telegram — UserIdInvalidError."""
    from core import tg_call
    api = _api(tmp_path, monkeypatch)
    api.book.upsert(CT.Contact(name="Sardorbek", telegram="@atabekovch", tg_id=1328351268))
    monkeypatch.setattr(tg_call, "_keys", lambda: {"call_to": "atabekovch"})
    monkeypatch.setattr(api, "precheck", lambda *a, **k: (api.book.find("Sardorbek")[0], ""))
    monkeypatch.setattr(api.me, "linked", lambda: True)
    calls = []
    api.call_fn = lambda target, name, text, **kw: calls.append((target, kw.get("via"))) or "ok"
    import threading
    monkeypatch.setattr(threading, "Thread", lambda target, **kw: type("T", (), {"start": lambda self: target()})())
    api.call("Sardorbek", "пора спать", urgent=True)
    assert calls == [("@atabekovch", None)]
