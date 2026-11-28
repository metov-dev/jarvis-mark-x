"""Блок [[PC]] — ИИ бота поручает дело Джарвису на ПК.

Раньше у ИИ бота не было способа что-то сделать на компьютере, а подсказка
велела писать «Передал команду на ПК». На «скажи вслух: привет, Сарварчик» и
«открой этот сайт на пк» он так и отвечал, а ПК ничего не получал (28.11).
Теперь ИИ пишет поручение в блоке [[PC]]…[[/PC]], бот отправляет его Джарвису
на ПК (со всеми его инструментами) и добавляет к ответу то, что тот реально
сделал. Без блока фраза «передаю команду на ПК» из ответа вырезается.
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger("pc_directive")

RE_PC = re.compile(r"\[\[PC\]\](.*?)\[\[/PC\]\]", re.S | re.I)

# Чего блок не запускает: выключение ПК, удаление, сообщения людям (для них —
# [[SEND]] с отменой), покупки. Это делается только явной командой владельца.
_UNSAFE = re.compile(
    r"(?:выключ\w*|выруб\w*|перезагру\w*|заверши\w*\s+работ\w*)\s+(?:компьютер|комп|пк|систем|windows)"
    r"|shutdown|reboot|перезагрузк|спящ\w+ режим|гибернац"
    r"|удал\w*|снеси|сотри|форматир|delete|\brm\b"
    r"|отправ\w*|напиши\s+\w+|позвони|сообщени|переведи\s+деньг|оплат|купи|пароль|\bключ\w*",
    re.I)

# Обещание без дела: «Передаю команду на ПК.», «Передал команду на ПК, жди результата».
_FALSE_CLAIM = re.compile(
    r"[^.!?\n]*(?:переда(?:ю|л|м)|отправ(?:ил|ляю))\s+(?:эту\s+)?команд\w*\s+(?:на\s+)?(?:пк|компьютер)[^.!?\n]*[.!?]?",
    re.I)


def extract(reply: str) -> tuple[str, str | None]:
    """(видимый текст без блока, поручение или None). Берём одно поручение."""
    m = RE_PC.search(reply or "")
    clean = RE_PC.sub("", reply or "").strip()
    if not m:
        return scrub(clean), None
    cmd = " ".join(m.group(1).split()).strip(" -•*")
    return clean, (cmd[:300] or None)


def scrub(text: str) -> str:
    """Убрать «передаю команду на ПК», если поручения не было."""
    out = _FALSE_CLAIM.sub("", text or "")
    return re.sub(r"\n{3,}", "\n\n", out).strip()


def is_safe(cmd: str) -> bool:
    return bool(cmd) and not _UNSAFE.search(cmd)


async def run(bridge, cmd: str, user_id: int) -> str:
    """Отдать поручение Джарвису на ПК. Строка с реальным итогом."""
    if not is_safe(cmd):
        logger.warning("[[PC]] отклонён: %r", cmd)
        return "⛔ Это я с телефона сам не делаю — скажите команду прямо: «/pc …»."
    if bridge is None or not bridge.connected:
        return "❌ Компьютер не в сети — команда не ушла. Включите на нём Джарвиса."
    res = await bridge.send_action("agent", user_id, timeout=45.0, request=cmd)
    if res is None:
        return "⚠ ПК не ответил за 45 с — проверьте Джарвиса на компьютере."
    if res.get("unknown"):
        # Старая сборка на ПК не знает «agent» — хотя бы разбор по словам.
        res = await bridge.send_command_full(cmd, user_id) or res
    text = (res.get("text") or "").strip()
    if not text or res.get("unknown"):
        return f"⚠ ПК не понял поручение «{cmd}»."
    return f"🖥 {text}"


async def apply(bridge, reply: str, user_id: int, *, trusted: bool = True) -> str:
    """Выполнить [[PC]] из ответа ИИ и вернуть текст для человека."""
    clean, cmd = extract(reply)
    if not cmd:
        return clean
    if not trusted:
        return scrub(clean)
    result = await run(bridge, cmd, user_id)
    return f"{clean}\n\n{result}".strip() if clean else result
