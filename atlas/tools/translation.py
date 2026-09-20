"""Sinhala / Tamil / English translation.

Two layers, because a public safety alert must not depend on a model being up:

* ``PhraseBook`` - fixed, reviewed wording for the standard alert sentences and
  vocabulary. Deterministic. Safety-critical fields (area, severity, event) come from
  structured data through it, never from free-text translation.
* ``LLMTranslator`` - optional, for free-text fields only, behind a small callable so
  the LLM client (``tools/llm.py``) can be plugged in without this module importing it.

The Sinhala and Tamil wording below needs review by a native speaker before any real
public use.
"""

from __future__ import annotations

import logging
from typing import Awaitable, Callable, Protocol

log = logging.getLogger(__name__)
LANGUAGES = ("si", "ta", "en")
BCP47 = {"si": "si-LK", "ta": "ta-LK", "en": "en-LK"}

EVENTS = {
    "flood": {"en": "Flood", "si": "ගංවතුර", "ta": "வெள்ளம்"},
    "landslide": {"en": "Landslide", "si": "නායයෑම්", "ta": "நிலச்சரிவு"},
    "cyclone": {"en": "Cyclone", "si": "සුළි කුණාටුව", "ta": "சூறாவளி"},
}
SEVERITY = {
    "Extreme": {"en": "Extreme", "si": "අතිශය දරුණු", "ta": "மிகக் கடுமையான"},
    "Severe": {"en": "Severe", "si": "දරුණු", "ta": "கடுமையான"},
    "Moderate": {"en": "Moderate", "si": "මධ්‍යම", "ta": "மிதமான"},
    "Minor": {"en": "Minor", "si": "සුළු", "ta": "சிறிய"},
}
INSTRUCTIONS = {
    "evacuate": {
        "en": "Move to higher ground or the nearest shelter immediately.",
        "si": "වහාම උස් බිමකට හෝ ආසන්නතම රැකවරණ ස්ථානයට යන්න.",
        "ta": "உடனடியாக உயரமான இடத்திற்கு அல்லது அருகிலுள்ள தங்குமிடத்திற்குச் செல்லுங்கள்.",
    },
    "avoid_area": {
        "en": "Avoid the affected area and follow official instructions.",
        "si": "බලපෑමට ලක් වූ ප්‍රදේශය මඟ හරින්න; නිල උපදෙස් පිළිපදින්න.",
        "ta": "பாதிக்கப்பட்ட பகுதியைத் தவிர்த்து, அதிகாரப்பூர்வ அறிவுறுத்தல்களைப் பின்பற்றுங்கள்.",
    },
    "prepare": {
        "en": "Prepare to evacuate and keep essential items ready.",
        "si": "ඉවත් වීමට සූදානම් වන්න; අත්‍යවශ්‍ය දෑ සූදානමින් තබා ගන්න.",
        "ta": "வெளியேறத் தயாராகுங்கள்; அத்தியாவசியப் பொருட்களை தயாராக வைத்திருங்கள்.",
    },
}
HEADLINE = {
    "en": "{event} warning: {area}",
    "si": "{event} අනතුරු ඇඟවීම: {area}",
    "ta": "{event} எச்சரிக்கை: {area}",
}
DESCRIPTION = {
    "en": "{event} warning for {area}. Severity: {severity}.",
    "si": "{area} සඳහා {event} අනතුරු ඇඟවීමකි. බරපතලකම: {severity}.",
    "ta": "{area} பகுதிக்கு {event} எச்சரிக்கை. தீவிரம்: {severity}.",
}
CANCELLED = {
    "en": "This alert has been cancelled.",
    "si": "මෙම අනතුරු ඇඟවීම අවලංගු කර ඇත.",
    "ta": "இந்த எச்சரிக்கை ரத்து செய்யப்பட்டுள்ளது.",
}
CANCEL_HEADLINE = {
    "en": "Alert cancelled: {area}",
    "si": "අනතුරු ඇඟවීම අවලංගුයි: {area}",
    "ta": "எச்சரிக்கை ரத்து: {area}",
}
SITREP_LABELS = {
    "title": {"en": "Situation report", "si": "තත්ත්ව වාර්තාව", "ta": "நிலைமை அறிக்கை"},
    "missions": {"en": "Active missions", "si": "සක්‍රීය මෙහෙයුම්", "ta": "செயலில் உள்ள பணிகள்"},
    "pending": {
        "en": "Awaiting approval",
        "si": "අනුමැතිය බලාපොරොත්තුවෙන්",
        "ta": "ஒப்புதலுக்காகக் காத்திருக்கிறது",
    },
    "alerts": {
        "en": "Alerts in force",
        "si": "බලපවත්නා අනතුරු ඇඟවීම්",
        "ta": "நடைமுறையில் உள்ள எச்சரிக்கைகள்",
    },
    "incident": {"en": "Incident", "si": "සිදුවීම", "ta": "சம்பவம்"},
}


class Translator(Protocol):
    async def translate(self, text: str, target: str, source: str = "en") -> str | None:
        """Return the translation, or None if this translator cannot produce one."""


class PhraseBook:
    """Deterministic rendering of the standard alert sentences."""

    languages = LANGUAGES

    def event(self, code: str, lang: str) -> str | None:
        return EVENTS.get(code.lower(), {}).get(lang)

    def severity(self, name: str, lang: str) -> str:
        return SEVERITY.get(name, {}).get(lang, name)

    def instruction(self, code: str, lang: str) -> str | None:
        return INSTRUCTIONS.get(code, {}).get(lang)

    def headline(self, event: str, area: str, lang: str) -> str:
        return HEADLINE[lang].format(event=event, area=area)

    def description(self, event: str, area: str, severity: str, lang: str) -> str:
        return DESCRIPTION[lang].format(event=event, area=area, severity=severity)

    def cancelled(self, lang: str) -> str:
        return CANCELLED[lang]

    def cancel_headline(self, area: str, lang: str) -> str:
        return CANCEL_HEADLINE[lang].format(area=area)

    def label(self, name: str, lang: str) -> str:
        return SITREP_LABELS[name][lang]


class LLMTranslator:
    """Free-text translation through an injected completion function.

    ``complete(prompt) -> str`` is whatever ``tools/llm.py`` exposes. Failures return
    None so callers can fall back instead of blocking a public alert.
    """

    NAMES = {"si": "Sinhala", "ta": "Tamil", "en": "English"}

    def __init__(self, complete: Callable[[str], Awaitable[str]]):
        self.complete = complete

    async def translate(self, text: str, target: str, source: str = "en") -> str | None:
        if target == source:
            return text
        prompt = (
            f"Translate this public safety message from {self.NAMES[source]} to "
            f"{self.NAMES[target]}. Keep numbers, place names and times exactly. Reply with "
            f"the translation only.\n\n{text}"
        )
        try:
            out = (await self.complete(prompt)).strip()
        except Exception:
            log.exception("LLM translation to %s failed", target)
            return None
        return out or None
