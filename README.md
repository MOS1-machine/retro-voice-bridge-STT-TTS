# MOS1 Voice Bridge

Local, fully offline speech pipeline: microphone -> speech recognition -> translation
(optional) -> text-to-speech -> virtual microphone output. Built for routing a
translated or synthesized voice into Discord, Steam Voice Chat, VRChat, Teamspeak,
OBS, or any other app that accepts a microphone input. Includes a macro panel with
global hotkeys for instant scripted phrases.

Everything runs on your machine. No cloud APIs, no accounts, no internet required
after the one-time model downloads.

![status](https://img.shields.io/badge/status-in%20dev-orange) ![platform](https://img.shields.io/badge/platform-Windows-blue)

## Features

STT via faster-whisper (tiny / base / small / medium / large-v3, selectable in the UI).
VAD-gated recording, so it reacts to full utterances instead of fixed time slices, with
filtering to suppress Whisper's known hallucinations on silence or background noise.
Optional offline translation via Argos Translate (RU <-> EN out of the box).
Two TTS backends: Windows SAPI5 (any voice installed system-wide) and Piper (open,
offline neural TTS with downloadable community voices).
Output routed to any audio device, intended for a virtual audio cable feeding Discord
or a game.
Live mode switching (RU-RU, RU-EN, EN-EN, EN-RU, AUTO) without restarting the pipeline.
Macro panel: bind a phrase to a global hotkey, works even when the window isn't
focused. A macro written in Russian is auto-translated on the fly if the active mode
expects English output, and vice versa (detected by script, no manual tagging needed).
Repeat-last-phrase hotkey, replays whatever was last spoken (from STT or from a macro).
New speech always interrupts whatever is still being spoken, instead of queueing.

## Requirements

Windows 10 or 11.
Python 3.11 or 3.12 recommended. 3.13 also works but needs the `webrtcvad-wheels`
package instead of plain `webrtcvad` (already reflected in requirements.txt) since
`webrtcvad` has no prebuilt wheel for 3.13 and needs a C++ compiler otherwise.
A virtual audio cable, for example [VB-Cable](https://vb-audio.com/Cable/), to expose
the synthesized voice as a microphone to other apps.

## 1. Install dependencies

Open a terminal in the project folder and run:

```
pip install -r requirements.txt
```

If you have more than one Python version installed, make sure `pip` and `python` (or
`py -X.Y`) point at the same interpreter, otherwise packages install into one Python
and the app looks for them in another. Check with:

```
where python
where pip
py -0p
```

## 2. Install the virtual audio cable

Download and install [VB-Cable](https://vb-audio.com/Cable/), reboot afterward. This
adds two devices to Windows: "CABLE Input" (a playback device the app writes to) and
"CABLE Output" (a recording device other apps, like Discord, read from).

## 3. Install offline translation packages (optional)

Only needed for RU-EN / EN-RU modes. Run once:

```
python install_translation.py
```

This downloads and installs the Argos Translate language packages for ru<->en. After
this, translation works fully offline.

## 4. Set up a voice

### Option A: SAPI5 (any voice already installed in Windows)

No extra files needed, the app reads the system's list of installed SAPI5 voices
directly. Only use voices you have a legitimate license for; the installer for any
properly licensed voice will register it in Windows automatically. If you don't have a
non-default voice, Microsoft's built-in ones (Irina, David, Hazel, etc.) work as-is for
testing the pipeline.

### Option B: Piper (open, offline, community voices)

Download the Windows build of Piper from the
[releases page](https://github.com/rhasspy/piper/releases) (`piper_windows_amd64.zip`)
and extract it into a `piper` folder inside the project.

Download a voice, matched `.onnx` + `.onnx.json` pair, into a `piper_models` folder.
Open Russian voices available at the time of writing (MIT licensed, hosted by the
Piper project):

```
https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/dmitri/medium/ru_RU-dmitri-medium.onnx
https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/dmitri/medium/ru_RU-dmitri-medium.onnx.json
https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/denis/medium/ru_RU-denis-medium.onnx
https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/denis/medium/ru_RU-denis-medium.onnx.json
https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/ruslan/medium/ru_RU-ruslan-medium.onnx
https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/ruslan/medium/ru_RU-ruslan-medium.onnx.json
```

Then point `config.json` at the files:

```json
"tts_backend": "piper",
"piper_exe": "C:\\path\\to\\project\\piper\\piper\\piper.exe",
"piper_model_ru": "C:\\path\\to\\project\\piper_models\\ru_RU-dmitri-medium.onnx"
```

## 5. Run

```
python main.py
```

Pick the mode, microphone, output device (the CABLE Input from step 2) and voice, then
press START. Point Discord's (or any other app's) microphone input at "CABLE Output".

## Macros

Add rows in the MACROS panel: a hotkey (in
[`keyboard`](https://github.com/boppreh/keyboard) library syntax, e.g. `f9`, `ctrl+1`,
`num 9`) and a phrase. Press SAVE to register the hotkeys globally. A macro phrase can
be written in Russian even while running in an English-output mode (or the reverse),
it gets auto-translated before being spoken. The REPEAT LAST hotkey (default `f8`)
replays whatever was last spoken, from either the live pipeline or a macro.

On Windows, global hotkeys sometimes require running the app as Administrator to work
outside the app window.

## Troubleshooting

**"Microsoft Visual C++ 14.0 or greater is required" while installing webrtcvad.**
Use `webrtcvad-wheels` instead of `webrtcvad` (already the default in
requirements.txt); it ships prebuilt wheels and needs no compiler.

**Window says "Not Responding" right after pressing START.**
The first run downloads the Whisper model in the background; this can take a while on
a slow connection but shouldn't freeze the UI (fixed by moving model loading off the
GUI thread). If it still hangs, run `python diagnose_whisper.py` to see exactly where
it stalls.

**Hallucinated text like repeated "subtitle credits" phrases on silence or noise.**
This is a known Whisper artifact from its training data. The VAD is tuned fairly
aggressive and Whisper's own `vad_filter` / `no_speech_threshold` are enabled to
suppress most of it, but it can't be eliminated completely with any Whisper size.

**A commercial voice installer fails with a license/verification error.**
That's the vendor's own license check, not something this project can or will help
bypass. Use a voice you actually hold a license for, a free system voice, or switch to
the Piper backend.

## Project layout

```
main.py                 GUI (PySide6), macros, hotkeys, config persistence
pipeline.py              VAD + STT (faster-whisper) + translation (Argos Translate)
tts_backends.py           SAPI5 and Piper TTS backends
install_translation.py      one-time Argos Translate package installer
diagnose_whisper.py          standalone Whisper load/diagnose script
config.json                    persisted settings (created on first run)
macros.json                     saved macros (created on first run)
```

## Roadmap

ESP32-S3 macropad (mechanical keys, OLED, RGB, USB HID) as a standalone device
emulating the same hotkeys. Per-game macro profiles. Voice effects layered on top of
TTS output (robot, radio, metallic). Optional full pipeline offload to a Raspberry Pi 4
as a standalone external module.
