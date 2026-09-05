"""Attachment (clipboard image paste) support on the embedded agent wire.

THE BUG THIS FILE EXISTS FOR (muxplex-1i9): the embedded runner turns the
client's message list into ``(history, prompt)`` via
``split_history_and_prompt``, and the FINAL ``role=user`` message is
flattened to a plain string by ``_extract_text``. ``_extract_text`` keeps
only ``type == "text"`` parts. So an OpenAI-shape multimodal message --

    {"role": "user", "content": [
        {"type": "text", "text": "what is wrong here?"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}},
    ]}

-- reached the model as the bare string ``"what is wrong here?"``. The
image was dropped **silently**: no error, no warning, no trace. The user
pastes a screenshot, watches it attach, sends it, and the model answers a
question it cannot see the subject of. That is precisely the "silent drop
/ confusing model reply" failure the item's acceptance criteria forbid.

THE SEAM: ``session.execute(prompt)`` takes a ``str`` -- the prompt half of
the wire is text-only and cannot be widened from here. But HISTORY is a
list of message dicts passed through ``_msg_to_dict`` (which preserves
``content`` verbatim, whatever its shape) and handed to the kernel's
context module via ``set_messages``. So history is the ONLY structural
seam in this path that can carry a content-block list at all.

Hence the fix: a final user message carrying image parts is kept WHOLE in
history and the turn continues with an empty prompt -- the same
``session.execute("")`` no-op-continuation path every tool-call
continuation already uses (see runner.py's module docstring). Nothing new
is invented; the multimodal message rides the mechanism that was already
proven.
"""

from __future__ import annotations

from typing import Any

from muxplex.agent_embedded.message_shape import (
    images_lost_reason,
    message_image_parts,
    split_history_and_prompt,
    unsupported_image_reason,
)


def _image_part(url: str = "data:image/png;base64,iVBORw0KGgo=") -> dict[str, Any]:
    return {"type": "image_url", "image_url": {"url": url}}


def _multimodal_user(text: str = "what is wrong here?") -> dict[str, Any]:
    return {
        "role": "user",
        "content": [{"type": "text", "text": text}, _image_part()],
    }


# ---------------------------------------------------------------------
# message_image_parts -- the detector
# ---------------------------------------------------------------------


def test_message_image_parts_finds_openai_image_url_block() -> None:
    parts = message_image_parts(_multimodal_user())
    assert len(parts) == 1, f"expected exactly one image part, got {parts!r}"
    assert parts[0]["type"] == "image_url"


def test_message_image_parts_is_empty_for_a_plain_string_message() -> None:
    assert message_image_parts({"role": "user", "content": "just text"}) == []


def test_message_image_parts_is_empty_for_a_text_only_block_list() -> None:
    msg = {"role": "user", "content": [{"type": "text", "text": "hi"}]}
    assert message_image_parts(msg) == []


def test_message_image_parts_tolerates_junk_content() -> None:
    """Never raise on a shape the client should not have sent."""
    assert message_image_parts({"role": "user"}) == []
    assert message_image_parts({"role": "user", "content": None}) == []
    assert message_image_parts({"role": "user", "content": [None, 7, "x"]}) == []


def test_message_image_parts_accepts_the_anthropic_native_block_name() -> None:
    """``{"type": "image"}`` is the provider-native spelling. Detecting it
    too costs nothing and means a future client that sends the native
    shape is not silently dropped by the very guard added to stop silent
    drops."""
    msg = {"role": "user", "content": [{"type": "image", "source": {}}]}
    assert len(message_image_parts(msg)) == 1


# ---------------------------------------------------------------------
# split_history_and_prompt -- the actual drop site
# ---------------------------------------------------------------------


def test_image_in_final_user_message_survives_into_history() -> None:
    """THE REGRESSION. Before the fix, history was ``messages[:-1]`` and the
    final message was flattened to text -- so the image existed nowhere in
    what the kernel was handed."""
    history, _prompt = split_history_and_prompt([_multimodal_user()])

    user_msgs = [m for m in history if m.get("role") == "user"]
    assert user_msgs, f"the multimodal user message vanished entirely: {history!r}"
    surviving = [p for m in user_msgs for p in message_image_parts(m)]
    assert surviving, (
        "the pasted image was dropped on the way to the model -- "
        f"history carries no image part: {history!r}"
    )
    # Shape-agnostic here on purpose: this test is about the image not
    # VANISHING. Which shape it arrives in is the separate, and equally
    # load-bearing, concern of
    # test_openai_image_url_is_rewritten_to_the_provider_native_block.
    assert "iVBORw0KGgo=" in repr(surviving[0]), (
        f"the image survived as a block but lost its data: {surviving[0]!r}"
    )


def test_image_turn_continues_with_an_empty_prompt() -> None:
    """The image rides in history, so the prompt must be empty -- otherwise
    the text would be sent twice (once in history, once as the prompt)."""
    _history, prompt = split_history_and_prompt([_multimodal_user()])
    assert prompt == "", f"expected the empty-prompt continuation path, got {prompt!r}"


def test_text_alongside_the_image_still_reaches_the_model() -> None:
    """The caption is the question. Dropping it would be a different silent
    failure with the same symptom."""
    history, _prompt = split_history_and_prompt([_multimodal_user("why is this red?")])
    blob = repr(history)
    assert "why is this red?" in blob, (
        f"the text typed alongside the image was lost: {history!r}"
    )


def test_earlier_multimodal_turns_keep_their_images_too() -> None:
    """A second turn re-POSTs the whole conversation. The first turn's image
    is then a history message, not the final one -- it must survive that
    path as well, or an attachment silently evaporates on the next thing
    the user types."""
    messages = [
        _multimodal_user("look at this"),
        {"role": "assistant", "content": "I see it."},
        {"role": "user", "content": "and now?"},
    ]
    history, prompt = split_history_and_prompt(messages)
    assert prompt == "and now?"
    surviving = [p for m in history for p in message_image_parts(m)]
    assert surviving, f"the earlier turn's image was dropped: {history!r}"


def test_plain_text_turn_is_completely_unchanged() -> None:
    """The overwhelmingly common path must not shift by one byte."""
    messages = [
        {"role": "system", "content": "you are a helpful agent"},
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
        {"role": "user", "content": "what sessions do I have?"},
    ]
    history, prompt = split_history_and_prompt(messages)
    assert prompt == "what sessions do I have?"
    assert [m["role"] for m in history] == ["user", "user", "assistant"]
    assert "<user_provided_instructions>" in history[0]["content"]


def test_tool_continuation_is_completely_unchanged() -> None:
    """A ``{role: "tool"}`` tail is a continuation and must keep taking the
    existing whole-list/empty-prompt path."""
    messages = [
        {"role": "user", "content": "run ls"},
        {"role": "assistant", "content": "", "tool_calls": []},
        {"role": "tool", "tool_call_id": "call_1", "content": "a.txt"},
    ]
    history, prompt = split_history_and_prompt(messages)
    assert prompt == ""
    assert [m["role"] for m in history] == ["user", "assistant", "tool"]


def test_image_only_message_with_no_caption_is_still_carried() -> None:
    """Pasting a screenshot and hitting send with no text is a real, normal
    thing to do."""
    msg = {"role": "user", "content": [_image_part()]}
    history, prompt = split_history_and_prompt([msg])
    assert prompt == ""
    assert [p for m in history for p in message_image_parts(m)]


# ---------------------------------------------------------------------
# Provider-shape normalization -- THE SECOND SILENT DROP
# ---------------------------------------------------------------------
# Getting the image into history is necessary but NOT sufficient. Read the
# Anthropic provider's own user-message branch (the provider this runner
# hardcodes -- see runner._PROVIDER_ID):
#
#     for block in content:
#         if block_type == "text":  content_blocks.append(...)
#         elif block_type == "image":
#             source = block.get("source", {})
#             if source.get("type") == "base64": content_blocks.append(...)
#     if content_blocks:
#         anthropic_messages.append({"role": "user", "content": content_blocks})
#
# There is no `else`. A block of any OTHER type -- including OpenAI's
# `image_url`, which is what an OpenAI-compatible face invites a client to
# send -- falls off the end of that loop and is discarded with no error and
# no log line. And because the append is guarded by `if content_blocks`, a
# user message whose ONLY block was an image_url does not merely lose its
# image: the entire message disappears from the conversation.
#
# Verified by reading two independently cached builds of
# amplifier-module-provider-anthropic: both handle `image` + base64 source
# only, and the string "image_url" appears zero times in either.
#
# So the panel's OpenAI-shape blocks must be rewritten into the provider's
# native shape before they are seeded, and anything that CANNOT be
# rewritten has to stop the turn out loud rather than ride into that
# silent-discard loop.


def test_openai_image_url_is_rewritten_to_the_provider_native_block() -> None:
    history, _prompt = split_history_and_prompt([_multimodal_user()])
    parts = [p for m in history for p in message_image_parts(m)]
    assert parts, "no image part survived at all"
    part = parts[0]
    assert part["type"] == "image", (
        f"the provider only understands type=image; got {part.get('type')!r}"
    )
    source = part.get("source")
    assert isinstance(source, dict), f"expected a source block, got {source!r}"
    assert source.get("type") == "base64", (
        f"the provider drops any source type but base64; got {source.get('type')!r}"
    )
    assert source.get("media_type") == "image/png", (
        f"media_type must come from the data URL, got {source.get('media_type')!r}"
    )
    assert source.get("data") == "iVBORw0KGgo=", (
        f"the base64 payload must survive intact, got {source.get('data')!r}"
    )


def test_a_jpeg_keeps_its_own_media_type() -> None:
    msg = {
        "role": "user",
        "content": [_image_part("data:image/jpeg;base64,/9j/4AAQSkZJRg==")],
    }
    history, _prompt = split_history_and_prompt([msg])
    part = [p for m in history for p in message_image_parts(m)][0]
    assert part["source"]["media_type"] == "image/jpeg"


def test_an_already_native_image_block_passes_through_unchanged() -> None:
    """Idempotence: a re-POSTed conversation runs this normalization again
    on blocks it already converted last turn."""
    native = {
        "role": "user",
        "content": [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": "iVBORw0KGgo=",
                },
            }
        ],
    }
    history, _prompt = split_history_and_prompt([native])
    part = [p for m in history for p in message_image_parts(m)][0]
    assert part["source"]["data"] == "iVBORw0KGgo="
    assert part["source"]["media_type"] == "image/png"


def test_text_blocks_are_untouched_by_normalization() -> None:
    history, _prompt = split_history_and_prompt([_multimodal_user("keep me")])
    texts = [
        p
        for m in history
        if isinstance(m.get("content"), list)
        for p in m["content"]
        if isinstance(p, dict) and p.get("type") == "text"
    ]
    assert texts and texts[0]["text"] == "keep me"


def test_the_normalized_message_survives_the_providers_own_loop() -> None:
    """End-to-end shape proof, without the provider installed.

    ``_provider_user_branch`` below is a faithful transcription of
    amplifier-module-provider-anthropic's user-message branch (read from
    two independently cached builds; both identical). Running our real
    normalized output through it proves the block we produce is one that
    branch actually keeps -- rather than one it drops on the floor, which
    is precisely what the pre-fix OpenAI shape did.

    If upstream ever changes that branch, this test does not magically
    know -- but it does state, in one readable place, the exact contract
    this code is written against, so the disagreement is findable.
    """

    def _provider_user_branch(content: Any) -> list[dict[str, Any]]:
        if not isinstance(content, list):
            return [{"type": "text", "text": content}]
        blocks: list[dict[str, Any]] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            if block_type == "text":
                blocks.append({"type": "text", "text": block.get("text", "")})
            elif block_type == "image":
                source = block.get("source", {})
                if source.get("type") == "base64":
                    blocks.append(
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": source.get("media_type", "image/jpeg"),
                                "data": source.get("data"),
                            },
                        }
                    )
            # NOTE: no else. This is the silent discard.
        return blocks

    history, _prompt = split_history_and_prompt([_multimodal_user("why is this red?")])
    user_msg = [m for m in history if m.get("role") == "user"][-1]
    survived = _provider_user_branch(user_msg["content"])

    kinds = [b["type"] for b in survived]
    assert "image" in kinds, (
        f"the provider's own loop would have discarded our image block: {user_msg!r}"
    )
    assert "text" in kinds, "the caption must survive the same loop"
    image = [b for b in survived if b["type"] == "image"][0]
    assert image["source"]["data"] == "iVBORw0KGgo="
    assert image["source"]["media_type"] == "image/png"


def test_the_pre_fix_shape_would_have_been_discarded_by_that_loop() -> None:
    """The counter-proof, so the test above cannot pass vacuously: the raw
    OpenAI block this panel used to send survives that loop as NOTHING --
    and an image-only message would vanish entirely, because the provider
    guards its append with ``if content_blocks:``."""

    def _provider_user_branch(content: list[Any]) -> list[dict[str, Any]]:
        blocks: list[dict[str, Any]] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                blocks.append(block)
        return blocks

    raw_openai_shape = [_image_part()]
    assert _provider_user_branch(raw_openai_shape) == [], (
        "this test is only meaningful if the un-normalized shape really is dropped"
    )


def test_a_remote_url_image_is_refused_rather_than_silently_discarded() -> None:
    """The provider can only carry base64. A plain http(s) image URL would
    hit the very no-else loop this whole section is about, so it must stop
    the turn instead."""
    msg = {"role": "user", "content": [_image_part("https://example.com/a.png")]}
    reason = unsupported_image_reason([msg])
    assert reason, "a non-base64 image URL must be refused, not passed along"
    assert "http" in reason.lower() or "url" in reason.lower(), (
        f"the refusal must explain what about it was unusable: {reason!r}"
    )


def test_an_unsupported_media_type_is_refused_by_name() -> None:
    msg = {"role": "user", "content": [_image_part("data:image/tiff;base64,AAAA")]}
    reason = unsupported_image_reason([msg])
    assert reason and "image/tiff" in reason, (
        f"the refusal must name the type the provider cannot take: {reason!r}"
    )


def test_supported_images_are_not_refused() -> None:
    assert unsupported_image_reason([_multimodal_user()]) is None


def test_a_text_only_conversation_is_never_refused() -> None:
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hello"},
    ]
    assert unsupported_image_reason(messages) is None


# ---------------------------------------------------------------------
# images_lost_reason -- the loud-failure decision
# ---------------------------------------------------------------------


def test_images_lost_reason_is_none_when_there_are_no_images() -> None:
    """No attachment, no new failure mode -- a context module without
    ``set_messages`` must keep degrading exactly as it did before (a
    logged warning), not start failing turns."""
    history = [{"role": "user", "content": "hello"}]
    assert images_lost_reason(history, can_seed=False) is None


def test_images_lost_reason_is_none_when_seeding_works() -> None:
    history = [_multimodal_user()]
    assert images_lost_reason(history, can_seed=True) is None


def test_images_lost_reason_explains_itself_when_seeding_is_unavailable() -> None:
    """If history cannot be seeded, an attached image reaches nothing. The
    turn must say so out loud rather than answering blind."""
    history = [_multimodal_user()]
    reason = images_lost_reason(history, can_seed=False)
    assert reason, "expected a refusal reason, got none"
    lowered = reason.lower()
    assert "attach" in lowered or "image" in lowered, (
        f"the reason must name what was lost: {reason!r}"
    )
    assert "1" in reason, f"the reason should say how many were lost: {reason!r}"


def test_images_lost_reason_counts_every_lost_image() -> None:
    history = [
        {
            "role": "user",
            "content": [{"type": "text", "text": "two"}, _image_part(), _image_part()],
        }
    ]
    reason = images_lost_reason(history, can_seed=False)
    assert reason is not None
    assert "2" in reason, f"expected the count of lost images in: {reason!r}"
