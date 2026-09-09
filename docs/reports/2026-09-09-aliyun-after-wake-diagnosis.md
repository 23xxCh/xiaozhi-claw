# No reply after wake

Serial wake-volume-serial.log captured nihaoxiaocan detection (score 0.387210),
listening at 14225498 ms, input ended at 14226538 ms, and reply-timeout at
14238538 ms. Gateway showed cancelled turns without final ASR text. This is
one approximately 1.04-second capture followed by the 12-second reply watchdog.
It does not prove whether the user was still speaking when capture ended.

Controlled real-provider probes used synthesized test speech:
- 2.6 seconds of 16 kHz PCM: final ASR at 3.69 seconds, response done at 7.06.
- Same query through raw Opus / StreamingOpusToPcm / SpeechToSpeechInput:
  final ASR at 4.17 seconds, response done at 7.92 seconds.
Times include upload, not user-end first-audio metrics. No speaker playback
was claimed. These probes establish the adapter/audio bridge can work but
do not validate the microphone or diagnose the physical failure alone.

NoCam source uses 4x mic gain, threshold 0.15 and VAD silence 100 ms. Logging
samples a short audio block every two seconds, so it cannot establish utterance
RMS or clipping. Do not lower the threshold or raise gain based on those sparse
samples. A second 50-second capture had no controlled wake utterance; user
confirmation about screen transition during speech remains pending.

No firmware modified/flashed. Requested additional Alibaba-only and
Volc-only ASR/TTS pipelines remain pending while input reliability is diagnosed.
