"""Image fidelity at the public SDK boundary, not private context seeding."""

from __future__ import annotations

import pytest

from muxplex.agent_embedded.errors import AgentRequestError
from muxplex.agent_embedded.message_shape import (
    content_parts,
    normalize_image_part,
    turn_input,
    validate_messages,
)

PNG = "iVBORw0KGgo="


def image(data=PNG, media="image/png"):
    return {"type": "image_url", "image_url": {"url": f"data:{media};base64,{data}"}}


@pytest.mark.parametrize(
    "media", ["image/png", "image/jpeg", "image/gif", "image/webp"]
)
def test_inline_image_retains_exact_media_and_base64(media):
    assert normalize_image_part(image(media=media)) == {
        "type": "image",
        "source": {"type": "base64", "media_type": media, "data": PNG},
    }


def test_native_image_is_idempotent():
    native = normalize_image_part(image())
    assert normalize_image_part(native) == native


@pytest.mark.parametrize(
    "part",
    [
        {"type": "image_url", "image_url": "https://example.invalid/image.png"},
        {"type": "image_url", "image_url": "file:///etc/passwd"},
        image(media="image/tiff"),
        image(data="garbage!"),
        image(data=""),
        image(data="aA==\n"),
        image(data="aB=="),  # noncanonical trailing bits
        {"type": "image", "source": {"type": "url", "url": "https://example.invalid"}},
        {
            "type": "image",
            "source": {"type": "base64", "media_type": [], "data": "AAAA"},
        },
    ],
)
def test_bad_images_fail_explicitly_before_sdk_creation(part):
    with pytest.raises(AgentRequestError) as refused:
        validate_messages(
            {"messages": [{"role": "user", "content": [part]}]}, browser=False
        )
    assert refused.value.code == "invalid_image"
    assert refused.value.status == 400


def test_public_sdk_input_keeps_image_in_current_turn_not_history():
    sdk = pytest.importorskip("amplifier_agent")
    content = [{"type": "text", "text": "Inspect"}, image()]
    result = turn_input(
        [{"role": "user", "content": content}], "claude-sonnet-5", browser=True
    )
    assert result.history is None
    assert result.content == [sdk.TextPart("Inspect"), sdk.ImagePart("image/png", PNG)]


def test_legacy_ordinary_history_keeps_prior_and_current_images():
    sdk = pytest.importorskip("amplifier_agent")
    messages = [
        {"role": "user", "content": [image()]},
        {"role": "assistant", "content": "Earlier reply"},
        {
            "role": "user",
            "content": [{"type": "text", "text": "Compare"}, image(media="image/jpeg")],
        },
    ]
    validate_messages({"messages": messages}, browser=False)
    result = turn_input(messages, "claude-sonnet-5", browser=False)
    assert result.history[0].content == [sdk.ImagePart("image/png", PNG)]
    assert result.content[-1] == sdk.ImagePart("image/jpeg", PNG)


@pytest.mark.parametrize(
    "content", [[{"type": "unknown"}], [{"type": []}], [None], None, ""]
)
def test_unknown_content_is_not_silently_dropped(content):
    with pytest.raises(AgentRequestError):
        validate_messages(
            {"messages": [{"role": "user", "content": content}]}, browser=False
        )


def test_content_parts_preserves_interleaving():
    sdk = pytest.importorskip("amplifier_agent")
    assert content_parts(
        [
            {"type": "text", "text": "before"},
            image(),
            {"type": "text", "text": "after"},
        ]
    ) == [
        sdk.TextPart("before"),
        sdk.ImagePart("image/png", PNG),
        sdk.TextPart("after"),
    ]
