import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import pytest

from atlas.tools.cap import (
    NS,
    CapAlert,
    CapArea,
    CapInfo,
    Category,
    Certainty,
    MsgType,
    Severity,
    Urgency,
    build_cap_xml,
    validate_cap_xml,
)

SENT = datetime(2026, 9, 20, 6, 30, 15, 999, tzinfo=timezone.utc)


def info(language="en-LK"):
    return CapInfo(
        language=language,
        categories=[Category.MET],
        event="Flood",
        urgency=Urgency.IMMEDIATE,
        severity=Severity.SEVERE,
        certainty=Certainty.LIKELY,
        headline="Flood warning: Ratnapura",
        areas=[CapArea(description="Ratnapura", circle="6.68,80.4 5")],
    )


def alert(**kw):
    return CapAlert(
        **{
            "identifier": "ATLAS-1",
            "sender": "atlas.claptac",
            "sent": SENT,
            "msg_type": MsgType.ALERT,
            "infos": [info()],
            **kw,
        }
    )


def test_built_alert_is_valid_cap():
    xml = build_cap_xml(alert())
    assert validate_cap_xml(xml) == []
    root = ET.fromstring(xml)
    assert root.tag == f"{{{NS}}}alert"
    assert root.find(f"{{{NS}}}sent").text == "2026-09-20T06:30:15+00:00"


def test_one_info_per_language():
    xml = build_cap_xml(alert(infos=[info("en-LK"), info("si-LK"), info("ta-LK")]))
    langs = [n.text for n in ET.fromstring(xml).iter(f"{{{NS}}}language")]
    assert langs == ["en-LK", "si-LK", "ta-LK"]


def test_non_ascii_text_survives():
    i = info("si-LK").model_copy(update={"headline": "ගංවතුර අනතුරු ඇඟවීම"})
    xml = build_cap_xml(alert(infos=[i]))
    assert "ගංවතුර" in xml and validate_cap_xml(xml) == []


def test_cancel_needs_references_and_references_format():
    with pytest.raises(ValueError, match="reference"):
        build_cap_xml(alert(msg_type=MsgType.CANCEL, infos=[]))
    xml = build_cap_xml(
        alert(msg_type=MsgType.CANCEL, infos=[], references=[("atlas.claptac", "ATLAS-0", SENT)])
    )
    assert "<references>atlas.claptac,ATLAS-0,2026-09-20T06:30:15+00:00</references>" in xml
    assert validate_cap_xml(xml) == []


def test_alert_without_info_is_refused():
    with pytest.raises(ValueError, match="info"):
        build_cap_xml(alert(infos=[]))


@pytest.mark.parametrize("bad", ["ATLAS 1", "a,b", "a<b", "a&b"])
def test_identifier_reserved_characters_rejected(bad):
    with pytest.raises(ValueError):
        alert(identifier=bad)


def test_naive_timestamp_rejected():
    with pytest.raises(ValueError):
        alert(sent=datetime(2026, 9, 20))


def test_validator_reports_problems():
    xml = build_cap_xml(alert())
    assert validate_cap_xml(xml.replace("<status>Actual</status>", "<status>Real</status>"))
    assert any("severity" in e for e in validate_cap_xml(xml.replace("Severe", "Awful")))
    assert any(
        "urgency" in e for e in validate_cap_xml(xml.replace("<urgency>Immediate</urgency>", ""))
    )
    assert validate_cap_xml("<not-xml") and validate_cap_xml("<alert/>")
    assert any(
        "areaDesc" in e for e in validate_cap_xml(xml.replace("<areaDesc>Ratnapura</areaDesc>", ""))
    )
    assert any(
        "sent" in e
        for e in validate_cap_xml(xml.replace("2026-09-20T06:30:15+00:00", "20 Sep 2026"))
    )
