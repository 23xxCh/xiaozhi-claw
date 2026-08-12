# Hensun face animation 2.0 specification

## Goal

Make the existing 60-scene face feel less mechanical by adding audio-reactive
mouth movement, natural blinking, restrained ambient gaze and eased state
entry. Keep the XiaoZhi protocol, camera, microphone and speaker configuration
unchanged.

## Interface and data flow

- Input: mono PCM samples that are already about to be written to the Hensun
  speaker, the device speaking state, the current face state and the 20 FPS face
  timer.
- Output: a 0-100 speech envelope plus bounded eye, mouth and entry transforms.
- Flow: decoded PCM -> Hensun board codec sampler -> atomic speech level ->
  LVGL-thread attack/release smoothing -> mouth height.

The audio task only samples absolute PCM amplitude and stores two atomic
integers. It never calls LVGL, allocates memory, takes a display lock or changes
the PCM data. The display consumes and smooths the level on its existing timer.

## Behaviour

- Sample every eighth PCM value. Apply a small noise floor and clamp the result
  to 0-100 before forwarding the unchanged PCM to the existing codec output.
- Use a short attack, a slower release and a stale-input timeout. Silence closes
  the mouth even while the logical device state is still `speaking`.
- Drive the mouth from PCM only during real speaking or the local showcase. The
  showcase retains a synthetic cadence so all scenes remain demonstrable.
- Schedule three-frame blinks at deterministic pseudo-random intervals. Do not
  blink already closed or sleeping eyes.
- Choose a new gaze target at slow pseudo-random intervals and move by at most
  one pixel per frame. Disable ambient gaze for sleep, alert, thinking and
  restrained safety families.
- Use integer ease-out entry motion; do not retain old face objects or allocate
  transition frame buffers.

## Safety and performance boundaries

- Content-safety and user-crisis scenes may lip-sync a spoken safety response,
  but their mouth opening is capped and they receive no ambient gaze or looping
  bounce.
- Blink lasts 150 ms at the existing 20 FPS and never flashes the full screen.
- Ambient gaze is limited to two pixels; existing thinking scan limits remain
  unchanged.
- PCM sampling performs no more than one absolute-value accumulation per eight
  samples and adds no heap allocation.
- Camera preview continues to hide the face layer. The acceptance targets stay
  below 8 ms average and 30 ms maximum LVGL render time.
