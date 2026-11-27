from datetime import date

from core import about_check as AC

TODAY = date(2026, 11, 28)
LABELS = {"name": "Имя", "city": "Город", "birthday": "День рождения"}


def test_plausible_profile_has_no_questions():
    ans = {"name": "Сардорбек", "address_as": "Сэр", "city": "Алматы", "birthday": "10 сентября 2006 года",
           "languages": "русский, казахский, узбекский", "occupation": "студент, программист, uxui дизайнер",
           "wake_time": "7:30", "club": "Реал Мадрид"}
    issues = AC.review(ans, [], LABELS, TODAY, geocode=lambda c: {"name": c},
                       clubs={"реал мадрид": ("Real Madrid", "Реал Мадрид")}, tg_name="Sardorbek")
    assert issues == []


def test_random_and_joke_answers_are_flagged():
    ans = {"birthday": "32 марта 1850", "wake_time": "25:99", "music": "фывфыв", "goals": "хз",
           "city": "Ыкслвжд", "club": "Бублики FC", "languages": "эльфийский"}
    issues = " | ".join(AC.review(ans, [], LABELS, TODAY, geocode=lambda c: None,
                                  clubs={"реал": ("Real Madrid", "Реал Мадрид")}))
    for key in ("birthday", "wake_time", "music", "goals", "city", "club", "languages"):
        assert f"key={key}" in issues, key


def test_contradiction_with_conversation_facts():
    issues = AC.review({"city": "Алматы"}, [("identity", "город", "Ташкент")], LABELS, TODAY,
                       geocode=lambda c: {"name": c})
    assert any("Ташкент" in i and "key=city" in i for i in issues)


def test_age_out_of_range():
    assert "лет" in AC.check_field("birthday", "1.1.2025", TODAY)


def test_time_ranges_are_not_gibberish():
    """Живая анкета 28.11: «9:00-14:00» помечалось как набор букв."""
    assert AC.check_field("work_hours", "9:00-14:00", TODAY) == ""
    assert AC.check_field("music", "Kanye West", TODAY) == ""


def test_confirmed_answer_removes_conflicting_fact(monkeypatch):
    """«Сергей» (ослышка 27.11) спорил с анкетой «Сардорбек»; подтверждение убирает спорный факт."""
    from core import about_me as AB
    mem = {"identity": {"name": "Сардорбек", "имя": "Сергей"}}

    class M:
        def load_memory(self):
            return mem

        def update_memory(self, upd):
            for c, kv in upd.items():
                mem.setdefault(c, {}).update(kv)

        def all_facts(self):
            return [(c, k, v) for c, kv in mem.items() for k, v in kv.items()]

        def forget(self, c, k, share=True):
            mem[c].pop(k, None)

    monkeypatch.setattr(AB, "_mem", lambda: M())
    monkeypatch.setattr(AB, "sync", lambda *a: None)
    monkeypatch.setattr(AB, "state", lambda: {"skipped": [], "intro": ""})
    AB.answer("name", "Сардорбек")
    assert mem["identity"] == {"name": "Сардорбек"}


def test_autofill_fills_only_empty_fields_and_skips_name(monkeypatch, tmp_path):
    """28.11: «пусть сам заполняет анкету» — из фактов разговора, только пустые поля."""
    from core import about_me as AB
    mem = {"identity": {"name": "Сардорбек", "имя": "Сергей", "город": "Алматы"},
           "preferences": {"любимый клуб": "Реал Мадрид", "музыка": "фывфыв"}}

    class M:
        def load_memory(self):
            return mem

        def update_memory(self, upd):
            for c, kv in upd.items():
                mem.setdefault(c, {}).update(kv)

        def all_facts(self):
            return [(c, k, v) for c, kv in list(mem.items()) for k, v in list(kv.items())]

        def forget(self, c, k, share=True):
            mem[c].pop(k, None)

    st = {"skipped": [], "intro": ""}
    monkeypatch.setattr(AB, "_mem", lambda: M())
    monkeypatch.setattr(AB, "sync", lambda *a: None)
    monkeypatch.setattr(AB, "state", lambda: dict(st))
    monkeypatch.setattr(AB, "_save_state", lambda s: st.update(s))
    filled = AB.autofill()
    assert set(filled) == {"city", "club"}                      # имя не трогаем, «фывфыв» — мусор
    assert AB.answers()["city"] == "Алматы" and AB.answers()["name"] == "Сардорбек"
    assert set(st["auto"]) == {"city", "club"}
