from atlas.tools.translation import (
    EVENTS,
    INSTRUCTIONS,
    LANGUAGES,
    SEVERITY,
    LLMTranslator,
    PhraseBook,
)


def test_every_phrase_exists_in_every_language():
    for table in (*EVENTS.values(), *SEVERITY.values(), *INSTRUCTIONS.values()):
        assert set(table) == set(LANGUAGES)
        assert all(text.strip() for text in table.values())


def test_headline_and_description_render_placeholders_in_all_languages():
    book = PhraseBook()
    for lang in LANGUAGES:
        event = book.event("flood", lang)
        text = book.headline(event, "Ratnapura", lang) + book.description(
            event, "Ratnapura", book.severity("Severe", lang), lang
        )
        assert "Ratnapura" in text and "{" not in text


def test_unknown_event_and_severity_fall_back():
    book = PhraseBook()
    assert book.event("meteor", "si") is None
    assert book.severity("Weird", "ta") == "Weird"


async def test_llm_translator_returns_text_and_skips_same_language():
    async def complete(prompt):
        assert "Sinhala" in prompt and "Keep numbers" in prompt
        return "  පරිවර්තනය  "

    translator = LLMTranslator(complete)
    assert await translator.translate("Bridge closed", "si") == "පරිවර්තනය"
    assert await translator.translate("Bridge closed", "en") == "Bridge closed"


async def test_llm_translator_failure_returns_none_not_an_exception():
    async def complete(prompt):
        raise TimeoutError

    assert await LLMTranslator(complete).translate("x", "ta") is None
