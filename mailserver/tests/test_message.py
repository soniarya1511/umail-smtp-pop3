import pytest

from mailserver.message import Message

LINES = [
    "Subject: Hello: world",
    "From: alice@posthawk.com",
    "To: charlie@swiftmail.com",
    "Date: 2025-02-20 10:30:00",
    "",
    "line one",
    "line two",
    "",
]


def test_parse_valid_message():
    msg = Message.parse(LINES)
    assert msg.subject == "Hello: world"
    assert msg.sender == "alice@posthawk.com"
    assert msg.recipient == "charlie@swiftmail.com"
    assert msg.body == "line one\nline two"


def test_round_trip():
    msg = Message.parse(LINES)
    assert Message.parse(msg.to_lines()) == msg


@pytest.mark.parametrize("index, bad", [
    (0, "Topic: x"),
    (1, "Sender: alice@posthawk.com"),
    (3, "Date: 2025-20-02"),
    (4, "not blank"),
])
def test_rejects_bad_layout(index, bad):
    lines = list(LINES)
    lines[index] = bad
    with pytest.raises(ValueError):
        Message.parse(lines)
