# Sameer app for StackChan

Adds a **SAMEER** app to the official [M5Stack StackChan](https://github.com/m5stack/StackChan)
firmware (CoreS3, ESP-IDF v5.5.4). The rest of the firmware stays as it is.

## What the robot does

1. Tap the screen or pat the head: Sameer looks up, thinking, and asks the server for a question (`/session_intro`).
2. It says the question out loud with a moving mouth (`/tts` returns 24 kHz PCM, the speaker's own rate).
3. It steps back and listens. Only loudness is measured, on the device: every ~10 s, or after 20 s of
   silence, it sends `elapsed_seconds`, `silence_seconds`, `talking` to `/session_followup`, and says
   the follow-up or wrap-up the server returns. Audio never leaves the device.
4. Tapping the screen while listening ends the session early.
5. It sends the participation numbers to `/evaluate_session`, then shows buttons 1, 2, 3; the choice
   goes to `/save_rating` and Sameer nods.

The camera is never used.

## Build

GitHub Actions builds it on every change under `firmware/`. Download **sameer-stackchan-firmware**
from the run's Artifacts. Optional repository settings:

- variable `SAMEER_SERVER_URL` (default `https://sameer-ai-agent-3.onrender.com`)
- secret `SAMEER_DEVICE_TOKEN`, matching `SAMEER_DEVICE_TOKEN` on the server

Locally, with ESP-IDF v5.5.4 on `PATH`: `firmware/build.sh` writes `firmware/build-sameer/sameer-stackchan.bin`.

## Flash

`sameer-stackchan.bin` is a single image for address `0x0`. In Chrome or Edge, open
<https://espressif.github.io/esptool-js/>, connect the StackChan over USB-C, and program the file at
`0x0`. **Do not erase the flash**, or the saved Wi-Fi settings are lost.

M5Stack's cloud features (StackChan World app, video calls) use keys that are not in the public
repository, so they don't work in this build. To go back to the factory firmware, use M5Burner.
