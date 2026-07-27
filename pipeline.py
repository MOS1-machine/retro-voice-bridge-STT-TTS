"""Voice pipeline: mic -> VAD -> STT (faster-whisper) -> translate (Argos, optional) -> TTS.

Runs in its own thread. Not time-sliced: once VAD detects the end of an utterance
(silence longer than SILENCE_MS_TO_CUT), the buffered audio goes to Whisper, then
optionally to the translator, then to TTS.
"""

import collections
import threading
import time

import numpy as np
import sounddevice as sd
import webrtcvad

from faster_whisper import WhisperModel

SAMPLE_RATE = 16000
FRAME_MS = 30
FRAME_SAMPLES = int(SAMPLE_RATE * FRAME_MS / 1000)
SILENCE_MS_TO_CUT = 300
MIN_UTTERANCE_MS = 400
RING_LEN = max(1, int(SILENCE_MS_TO_CUT / FRAME_MS))


class PipelineState:
    IDLE = "IDLE"
    LOADING = "LOADING"
    LISTENING = "LISTENING"
    PROCESSING = "PROCESSING"
    SPEAKING = "SPEAKING"


# mode -> (Whisper input language, TTS output language); None means auto-detect.
MODE_RULES = {
    "RU-RU": ("ru", "ru"),
    "EN-EN": ("en", "en"),
    "RU-EN": ("ru", "en"),
    "EN-RU": ("en", "ru"),
    "AUTO": (None, None),
}


# module-level translator cache, shared by VoicePipeline and macro playback
_translator_cache = {}


def get_translator(src, dst):
    key = (src, dst)
    if key in _translator_cache:
        return _translator_cache[key]
    import argostranslate.translate as at
    installed = at.get_installed_languages()
    from_lang = next((l for l in installed if l.code == src), None)
    to_lang = next((l for l in installed if l.code == dst), None)
    if not from_lang or not to_lang:
        raise RuntimeError(
            f"Argos Translate: package {src}->{dst} not installed "
            f"(run install_translation.py)"
        )
    translation = from_lang.get_translation(to_lang)
    _translator_cache[key] = translation
    return translation


class VoicePipeline(threading.Thread):
    """
    tts_speak_fn(text, lang): synthesizes and plays audio (see tts_backends.py).
    on_state(state): called on pipeline state changes.
    on_log(kind, text, lang): kind is "stt" | "translate" | "error".
    """

    def __init__(self, mode, input_device, tts_speak_fn, on_state, on_log,
                 whisper_model_size="small", vad_aggressiveness=3):
        super().__init__(daemon=True)
        self.mode = mode
        self.input_device = input_device
        self.tts_speak_fn = tts_speak_fn
        self.on_state = on_state
        self.on_log = on_log
        self._stop_flag = threading.Event()
        self._vad_aggressiveness = vad_aggressiveness
        self._whisper_model_size = whisper_model_size
        self.vad = None
        self.model = None

    def stop(self):
        self._stop_flag.set()

    def run(self):
        # Model loading happens here (in the background thread), not in __init__,
        # so the GUI thread never blocks on it.
        self.on_state(PipelineState.LOADING)
        try:
            self.vad = webrtcvad.Vad(self._vad_aggressiveness)
            self.model = WhisperModel(self._whisper_model_size, device="cpu", compute_type="int8")
        except Exception as e:
            self.on_log("error", f"Failed to load Whisper model: {e}", "-")
            self.on_state(PipelineState.IDLE)
            return

        self.on_state(PipelineState.LISTENING)
        ring = collections.deque(maxlen=RING_LEN)
        voiced_frames = []
        triggered = {"value": False}

        def callback(indata, frames, time_info, status):
            if self._stop_flag.is_set():
                raise sd.CallbackStop()
            pcm16 = (indata[:, 0] * 32767.0).astype(np.int16).tobytes()
            try:
                is_speech = self.vad.is_speech(pcm16, SAMPLE_RATE)
            except Exception:
                is_speech = False

            if not triggered["value"]:
                ring.append((pcm16, is_speech))
                num_voiced = len([f for f, s in ring if s])
                if ring.maxlen and num_voiced > 0.6 * ring.maxlen:
                    triggered["value"] = True
                    voiced_frames.extend(f for f, _ in ring)
                    ring.clear()
            else:
                voiced_frames.append(pcm16)
                ring.append((pcm16, is_speech))
                num_unvoiced = len([f for f, s in ring if not s])
                if ring.maxlen and num_unvoiced > 0.9 * ring.maxlen:
                    triggered["value"] = False
                    audio_bytes = b"".join(voiced_frames)
                    duration_ms = len(voiced_frames) * FRAME_MS
                    voiced_frames.clear()
                    ring.clear()
                    if duration_ms >= MIN_UTTERANCE_MS:
                        threading.Thread(
                            target=self._process_utterance, args=(audio_bytes,), daemon=True
                        ).start()

        with sd.InputStream(
            samplerate=SAMPLE_RATE,
            blocksize=FRAME_SAMPLES,
            channels=1,
            dtype="float32",
            device=self.input_device,
            callback=callback,
        ):
            while not self._stop_flag.is_set():
                time.sleep(0.1)

        self.on_state(PipelineState.IDLE)

    def _process_utterance(self, audio_bytes):
        self.on_state(PipelineState.PROCESSING)
        audio_np = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        src_lang, dst_lang = MODE_RULES[self.mode]

        # vad_filter/no_speech_threshold/condition_on_previous_text: reduce
        # Whisper hallucinations ("subtitle credits" text) on silence/noise.
        segments, info = self.model.transcribe(
            audio_np,
            language=src_lang,
            beam_size=1,
            vad_filter=True,
            no_speech_threshold=0.6,
            log_prob_threshold=-1.0,
            condition_on_previous_text=False,
        )
        text = "".join(seg.text for seg in segments).strip()
        detected_lang = src_lang or info.language

        if not text:
            self.on_state(PipelineState.LISTENING)
            return

        self.on_log("stt", text, detected_lang)

        out_text, out_lang = text, detected_lang
        target_lang = dst_lang or detected_lang

        if target_lang != detected_lang:
            try:
                translation = get_translator(detected_lang, target_lang)
                out_text = translation.translate(text)
                out_lang = target_lang
                self.on_log("translate", out_text, out_lang)
            except Exception as e:
                self.on_log("error", str(e), detected_lang)

        self.on_state(PipelineState.SPEAKING)
        try:
            self.tts_speak_fn(out_text, out_lang)
        except Exception as e:
            self.on_log("error", f"TTS: {e}", out_lang)
        self.on_state(PipelineState.LISTENING)
