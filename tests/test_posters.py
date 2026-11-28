"""Постеры «Кино»: только с Киного, один раз, с диска."""
import io

from PIL import Image

from core import posters


class _Resp:
    def __init__(self, text="", data=b""):
        self.text, self.raw = text, io.BytesIO(data)
        self.raw.read = lambda n=-1, decode_content=True, _b=self.raw: io.BytesIO.read(_b, n)

    def raise_for_status(self):
        pass


def _jpeg():
    b = io.BytesIO()
    Image.new("RGB", (60, 90), "red").save(b, "JPEG")
    return b.getvalue()


def test_downloads_once_from_kinogo(tmp_path, monkeypatch):
    import requests
    monkeypatch.setattr(posters, "_dir", lambda: tmp_path)
    calls = []

    def get(url, **kw):
        calls.append(url)
        if url.endswith(".html"):
            return _Resp('<meta property="og:image" content="/uploads/p.webp">')
        return _Resp(data=_jpeg())
    monkeypatch.setattr(requests, "get", get)
    p = posters.fetch("Игра престолов", "https://kz5.kinogo-films.lol/5059-igra-prestolov.html")
    assert p and p.is_file()
    assert calls[0].startswith("https://kz6.")             # старое зеркало → нынешнее
    assert posters.fetch("Игра престолов", "https://kz5.kinogo-films.lol/x.html") == p
    assert len(calls) == 2                                # второй раз — с диска


def test_foreign_image_is_not_downloaded(tmp_path, monkeypatch):
    import requests
    monkeypatch.setattr(posters, "_dir", lambda: tmp_path)
    calls = []

    def get(url, **kw):
        calls.append(url)
        return _Resp('<meta property="og:image" content="https://evil.example/x.jpg">')
    monkeypatch.setattr(requests, "get", get)
    assert posters.fetch("Менталист", "https://kz6.kinogo-films.lol/2817-mentalist.html") is None
    assert len(calls) == 1
