# Sameer app for StackChan

Adds a **SAMEER** app to the official [M5Stack StackChan](https://github.com/m5stack/StackChan)
firmware (CoreS3, ESP-IDF v5.5.4). The rest of the firmware stays as it is.

## What the robot does

1. Tap the screen or pat the head: Sameer looks up, thinking, and asks the server for a question (`/session_intro`).
2. It says the question out loud with a moving mouth (`/tts` returns 24 kHz PCM, the speaker's own rate).
3. It listens. Loudness is measured on the device to find when someone starts and stops talking.
   Each finished turn (a pause of 1.2 s after at least 0.6 s of speech, up to 12 s) is sent as
   12 kHz WAV to `/converse`. The server understands it in memory (audio is never stored) and
   decides: **reply** (the robot answers out loud), **listen** (stays quiet while the family talks
   among themselves) or **wrap_up** (says goodbye and goes to rating).
4. After 20 s of silence it also asks `/session_followup`, which can add a follow-up question.
   Tapping the screen while listening ends the session.
5. It sends the participation numbers to `/evaluate_session`, then shows buttons 1, 2, 3; the choice
   goes to `/save_rating` and the robot nods.

The RGB bar glows gold only while the microphone is listening. The camera is never used.

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
