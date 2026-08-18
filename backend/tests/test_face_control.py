from backend.realtime.face_control import FaceControlParser


def collect(parser: FaceControlParser, *chunks: str) -> tuple[list[str], list[str]]:
    text: list[str] = []
    emotions: list[str] = []
    for chunk in chunks:
        for event in parser.feed(chunk):
            if event.kind == "text":
                text.append(event.value)
            else:
                emotions.append(event.value)
    for event in parser.flush():
        if event.kind == "text":
            text.append(event.value)
        else:
            emotions.append(event.value)
    return text, emotions


def test_control_marker_may_be_split_across_stream_chunks() -> None:
    text, emotions = collect(
        FaceControlParser(),
        "[[fa",
        "ce:hap",
        "py]]你今天看起来很有精神。",
    )

    assert "".join(text) == "你今天看起来很有精神。"
    assert emotions == ["happy"]


def test_second_marker_is_allowed_only_after_a_sentence_boundary() -> None:
    parser = FaceControlParser()
    text, emotions = collect(
        parser,
        "[[face:caring]]先慢慢来。",
        "[[face:curious]]你愿意说说发生了什么吗？",
    )

    assert "".join(text) == "先慢慢来。你愿意说说发生了什么吗？"
    assert emotions == ["caring", "curious"]


def test_mid_sentence_or_third_marker_is_removed_without_changing_emotion() -> None:
    text, emotions = collect(
        FaceControlParser(),
        "[[face:happy]]今天[[face:surprised]]天气不错。",
        "[[face:curious]]要不要出去走走？",
        "[[face:laughing]]哈哈。",
    )

    assert "".join(text) == "今天天气不错。要不要出去走走？哈哈。"
    assert emotions == ["happy", "curious"]


def test_unknown_and_malformed_markers_never_reach_spoken_text() -> None:
    text, emotions = collect(
        FaceControlParser(),
        "[[face:excited]]你好。",
        "[[face:happy]再见。",
    )

    assert "".join(text) == "你好。再见。"
    assert emotions == []


def test_plain_text_is_streamed_when_model_omits_marker() -> None:
    parser = FaceControlParser()

    first = parser.feed("普通回复")
    tail = parser.flush()

    assert [(event.kind, event.value) for event in first + tail] == [("text", "普通回复")]


def test_all_supported_conversation_emotions_are_accepted() -> None:
    supported = {
        "neutral",
        "happy",
        "laughing",
        "caring",
        "affectionate",
        "curious",
        "surprised",
        "confused",
        "concerned",
        "apologetic",
    }

    for emotion in supported:
        text, emotions = collect(FaceControlParser(), f"[[face:{emotion}]]收到。")
        assert "".join(text) == "收到。"
        assert emotions == [emotion]
