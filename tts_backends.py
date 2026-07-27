"""Two TTS backends.

SAPI5Backend: any Windows SAPI5 voice (Microsoft, or a legitimately installed
third-party voice), via win32com. Requires pywin32 and Windows.

PiperBackend: fully open offline engine. Requires a separately downloaded
piper.exe and .onnx voice models (https://github.com/rhasspy/piper).
"""

import os
import subprocess
import tempfile
import wave

import numpy as np
import sounddevice as sd


def list_output_devices():
    devices = sd.query_devices()
    return [(i, d["name"]) for i, d in enumerate(devices) if d["max_output_channels"] > 0]


def list_input_devices():
    devices = sd.query_devices()
    return [(i, d["name"]) for i, d in enumerate(devices) if d["max_input_channels"] > 0]


class SAPI5Backend:
    """SAPI5 voice, Windows only."""

    # SVSFlagsAsync (1) + SVSFPurgeBeforeSpeak (2): don't wait for the current
    # phrase to finish, interrupt it and start speaking the new one right away.
    SPEAK_FLAGS_INTERRUPT = 1 | 2

    def __init__(self):
        import win32com.client
        self._win32com = win32com.client
        self.voice = self._win32com.Dispatch("SAPI.SpVoice")

    def list_voices(self):
        tokens = self.voice.GetVoices()
        return [tokens.Item(i).GetDescription() for i in range(tokens.Count)]

    def set_voice(self, name_substring):
        tokens = self.voice.GetVoices()
        for i in range(tokens.Count):
            desc = tokens.Item(i).GetDescription()
            if name_substring.lower() in desc.lower():
                self.voice.Voice = tokens.Item(i)
                return True
        return False

    def set_rate(self, rate):
        self.voice.Rate = int(rate)  # SAPI5 range: -10 (slow) .. 10 (fast)

    def set_volume(self, volume):
        self.voice.Volume = int(volume)  # SAPI5 range: 0..100

    def speak_to_device(self, text, lang=None, output_device_name=None):
        if output_device_name:
            audio_out_cat = self._win32com.Dispatch("SAPI.SpObjectTokenCategory")
            audio_out_cat.SetId(
                r"HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Speech\AudioOutput", False
            )
            tokens = audio_out_cat.EnumerateTokens()
            for i in range(tokens.Count):
                tok = tokens.Item(i)
                if output_device_name.lower() in tok.GetDescription().lower():
                    self.voice.AudioOutput = tok
                    break
        self.voice.Speak(text, self.SPEAK_FLAGS_INTERRUPT)


class PiperBackend:
    """Piper TTS via external binary, output routed through sounddevice."""

    def __init__(self, piper_exe, model_paths):
        self.piper_exe = piper_exe        # path to piper.exe / piper
        self.model_paths = model_paths     # {"ru": "path/xx.onnx", "en": "path/xx.onnx"}

    def synthesize(self, text, lang):
        model_path = self.model_paths.get(lang)
        if not model_path or not os.path.exists(model_path):
            raise RuntimeError(f"Piper: no voice model configured for language '{lang}'")
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            out_path = tmp.name
        try:
            subprocess.run(
                [self.piper_exe, "--model", model_path, "--output_file", out_path],
                input=text.encode("utf-8"),
                check=True,
                capture_output=True,
            )
            with wave.open(out_path, "rb") as wf:
                sr = wf.getframerate()
                data = wf.readframes(wf.getnframes())
        finally:
            if os.path.exists(out_path):
                os.unlink(out_path)
        audio = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
        return audio, sr

    def speak_to_device(self, text, lang, output_device=None):
        audio, sr = self.synthesize(text, lang)
        sd.stop()  # interrupt whatever the previous phrase is still playing
        sd.play(audio, samplerate=sr, device=output_device)
