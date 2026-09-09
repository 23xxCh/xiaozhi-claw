# Partial news speech investigation

User reported visible news text with part of the speech missing.
Turn b3f6e113-5da4-48a2-8e77-44d989d09ddc completed web_search in
2843 ms and reported completed playback, first device PCM at 6701 ms.
No exception/cancellation was recorded for that turn. These events alone
do not demonstrate audible completeness.

Inspection found all non-story responses limited to 60 spoken characters,
with hard slicing in speak(). News now has an explicit short three-item
policy (220-character prompt target, 240-character hard budget). Existing
ordinary chat/story budgets remain. Generated/spoken character counts and
budget are logged without conversation text to detect future clipping.
This addresses a confirmed truncation risk, not proof of this user's exact
audio loss cause; displayed sentence text also passes through speak().

38 provider/parameter tests passed, including news-budget regression.
Serial capture run/reports/news-audio-serial.log showed a short reply with
decode drops=0 and return to listening; it was not a confirmed news replay.
Long-reply listening acceptance remains pending. No firmware changed.
