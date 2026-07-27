"""MOS1 Voice Bridge: local STT -> translate -> TTS -> virtual mic bridge with macros."""

import json
import sys
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from pipeline import VoicePipeline, PipelineState, get_translator
from tts_backends import SAPI5Backend, PiperBackend, list_input_devices, list_output_devices

APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = APP_DIR / "config.json"
MACROS_PATH = APP_DIR / "macros.json"

DEFAULT_CONFIG = {
    "mode": "RU-RU",
    "input_device": None,
    "output_device": None,
    "tts_backend": "sapi5",
    "sapi5_voice": "",
    "piper_exe": "",
    "piper_model_ru": "",
    "piper_model_en": "",
    "rate": 0,
    "volume": 100,
    "repeat_hotkey": "f8",
}


def load_json(path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return json.loads(json.dumps(default))
    return json.loads(json.dumps(default))


def save_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


STYLE = """
QWidget { background-color: #0a0e17; color: #d8dee9; font-family: 'Consolas', 'JetBrains Mono', monospace; font-size: 12px; }
#Header { background-color: #0a0e17; border-bottom: 1px solid #1c2433; }
#Logo { color: #ff8a1f; font-size: 22px; font-weight: bold; letter-spacing: 3px; }
#StatusLabel { color: #9aa5b1; font-size: 11px; letter-spacing: 1px; }
QGroupBox {
    border: 1px solid #1c2433; border-radius: 4px; margin-top: 16px;
    background-color: #0d121c; padding: 12px; font-size: 11px; color: #9aa5b1;
}
QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; color: #9aa5b1; letter-spacing: 2px; }
QPushButton {
    background-color: #10151f; color: #ff8a1f; border: 1px solid #ff8a1f;
    padding: 6px 14px; border-radius: 3px; font-weight: bold; letter-spacing: 1px;
}
QPushButton:hover { background-color: #ff8a1f; color: #0a0e17; }
QPushButton:disabled { color: #4a5568; border-color: #2a3140; }
QComboBox, QLineEdit, QSpinBox, QSlider {
    background-color: #10151f; border: 1px solid #2a3140; padding: 4px; color: #d8dee9;
}
QTableWidget {
    background-color: #10151f; border: 1px solid #2a3140; gridline-color: #1c2433;
}
QHeaderView::section {
    background-color: #0d121c; color: #9aa5b1; border: none; padding: 4px; letter-spacing: 1px;
}
QTextEdit { background-color: #10151f; border: 1px solid #2a3140; color: #d8dee9; }
QLabel#Badge {
    color: #ff8a1f; border: 1px solid #ff8a1f; padding: 1px 8px; border-radius: 2px; font-size: 10px;
}
"""

MODES = ["RU-RU", "RU-EN", "EN-EN", "EN-RU", "AUTO"]


class HotkeySignal(QtCore.QObject):
    triggered = QtCore.Signal(str)
    repeat_triggered = QtCore.Signal()


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("MOS1 // VOICE BRIDGE")
        self.resize(980, 720)

        self.config = load_json(CONFIG_PATH, DEFAULT_CONFIG)
        self.macros = load_json(MACROS_PATH, [])  # [{"hotkey": str, "phrase": str}]

        self.pipeline = None
        self.tts_backend = None
        self.last_spoken = None  # (text, lang) of the last phrase actually spoken
        self.hotkey_signal = HotkeySignal()
        self.hotkey_signal.triggered.connect(self._play_macro_by_hotkey)
        self.hotkey_signal.repeat_triggered.connect(self._repeat_last)
        self._registered_hotkeys = []
        self._repeat_hotkey_registered = None

        self._build_ui()
        self._populate_devices()
        self._rebuild_tts_backend()
        self._reload_macro_table()
        self._register_all_hotkeys()

    # ---------- UI ----------

    def _build_ui(self):
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        root = QtWidgets.QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)

        header = QtWidgets.QWidget()
        header.setObjectName("Header")
        header_layout = QtWidgets.QHBoxLayout(header)
        header_layout.setContentsMargins(20, 16, 20, 16)
        logo = QtWidgets.QLabel("MOS1 // VOICE BRIDGE")
        logo.setObjectName("Logo")
        header_layout.addWidget(logo)
        header_layout.addStretch()
        self.status_dot = QtWidgets.QLabel("●")
        self.status_dot.setStyleSheet("color: #4a5568; font-size: 14px;")
        self.status_text = QtWidgets.QLabel("SYSTEM STATUS: IDLE")
        self.status_text.setObjectName("StatusLabel")
        header_layout.addWidget(self.status_dot)
        header_layout.addWidget(self.status_text)
        root.addWidget(header)

        body = QtWidgets.QHBoxLayout()
        body.setContentsMargins(20, 16, 20, 16)
        body.setSpacing(16)
        root.addLayout(body)

        body.addWidget(self._build_pipeline_group(), 1)
        body.addWidget(self._build_tts_group(), 1)
        body.addWidget(self._build_macro_group(), 1)

    def _build_pipeline_group(self):
        box = QtWidgets.QGroupBox("PIPELINE")
        layout = QtWidgets.QVBoxLayout(box)

        row1 = QtWidgets.QHBoxLayout()
        row1.addWidget(QtWidgets.QLabel("Mode:"))
        self.mode_combo = QtWidgets.QComboBox()
        self.mode_combo.addItems(MODES)
        self.mode_combo.setCurrentText(self.config.get("mode", "RU-RU"))
        row1.addWidget(self.mode_combo)
        layout.addLayout(row1)

        row1b = QtWidgets.QHBoxLayout()
        row1b.addWidget(QtWidgets.QLabel("STT model:"))
        self.whisper_combo = QtWidgets.QComboBox()
        self.whisper_combo.addItems(["tiny", "base", "small", "medium", "large-v3"])
        self.whisper_combo.setCurrentText(self.config.get("whisper_model", "small"))
        row1b.addWidget(self.whisper_combo)
        layout.addLayout(row1b)

        row2 = QtWidgets.QHBoxLayout()
        row2.addWidget(QtWidgets.QLabel("Microphone:"))
        self.input_combo = QtWidgets.QComboBox()
        row2.addWidget(self.input_combo)
        layout.addLayout(row2)

        row3 = QtWidgets.QHBoxLayout()
        row3.addWidget(QtWidgets.QLabel("Output (virtual mic):"))
        self.output_combo = QtWidgets.QComboBox()
        row3.addWidget(self.output_combo)
        layout.addLayout(row3)

        self.start_stop_btn = QtWidgets.QPushButton("START")
        self.start_stop_btn.clicked.connect(self._toggle_pipeline)
        layout.addWidget(self.start_stop_btn)

        layout.addWidget(QtWidgets.QLabel("Log:"))
        self.log_view = QtWidgets.QTextEdit()
        self.log_view.setReadOnly(True)
        layout.addWidget(self.log_view, 1)

        return box

    def _build_tts_group(self):
        box = QtWidgets.QGroupBox("TTS VOICE")
        layout = QtWidgets.QVBoxLayout(box)

        row1 = QtWidgets.QHBoxLayout()
        row1.addWidget(QtWidgets.QLabel("Engine:"))
        self.backend_combo = QtWidgets.QComboBox()
        self.backend_combo.addItems(["sapi5", "piper"])
        self.backend_combo.setCurrentText(self.config.get("tts_backend", "sapi5"))
        self.backend_combo.currentTextChanged.connect(self._on_backend_changed)
        row1.addWidget(self.backend_combo)
        layout.addLayout(row1)

        row2 = QtWidgets.QHBoxLayout()
        row2.addWidget(QtWidgets.QLabel("Voice (SAPI5):"))
        self.voice_combo = QtWidgets.QComboBox()
        row2.addWidget(self.voice_combo)
        layout.addLayout(row2)

        row3 = QtWidgets.QHBoxLayout()
        row3.addWidget(QtWidgets.QLabel("Rate:"))
        self.rate_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.rate_slider.setRange(-10, 10)
        self.rate_slider.setValue(int(self.config.get("rate", 0)))
        row3.addWidget(self.rate_slider)
        layout.addLayout(row3)

        row4 = QtWidgets.QHBoxLayout()
        row4.addWidget(QtWidgets.QLabel("Volume:"))
        self.volume_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(int(self.config.get("volume", 100)))
        row4.addWidget(self.volume_slider)
        layout.addLayout(row4)

        self.test_voice_btn = QtWidgets.QPushButton("TEST VOICE")
        self.test_voice_btn.clicked.connect(self._test_voice)
        layout.addWidget(self.test_voice_btn)

        layout.addStretch()

        return box

    def _build_macro_group(self):
        box = QtWidgets.QGroupBox("MACROS")
        layout = QtWidgets.QVBoxLayout(box)

        self.macro_table = QtWidgets.QTableWidget(0, 2)
        self.macro_table.setHorizontalHeaderLabels(["HOTKEY", "PHRASE"])
        self.macro_table.horizontalHeader().setSectionResizeMode(
            1, QtWidgets.QHeaderView.Stretch
        )
        layout.addWidget(self.macro_table, 1)

        btn_row = QtWidgets.QHBoxLayout()
        add_btn = QtWidgets.QPushButton("ADD")
        add_btn.clicked.connect(self._add_macro_row)
        remove_btn = QtWidgets.QPushButton("REMOVE")
        remove_btn.clicked.connect(self._remove_macro_row)
        save_btn = QtWidgets.QPushButton("SAVE")
        save_btn.clicked.connect(self._save_macros)
        btn_row.addWidget(add_btn)
        btn_row.addWidget(remove_btn)
        btn_row.addWidget(save_btn)
        layout.addLayout(btn_row)

        repeat_row = QtWidgets.QHBoxLayout()
        repeat_row.addWidget(QtWidgets.QLabel("Repeat hotkey:"))
        self.repeat_hotkey_edit = QtWidgets.QLineEdit(self.config.get("repeat_hotkey", "f8"))
        repeat_row.addWidget(self.repeat_hotkey_edit)
        repeat_btn = QtWidgets.QPushButton("REPEAT LAST")
        repeat_btn.clicked.connect(self._repeat_last)
        repeat_row.addWidget(repeat_btn)
        layout.addLayout(repeat_row)

        return box

    # ---------- Devices and TTS ----------

    def _populate_devices(self):
        inputs = list_input_devices()
        outputs = list_output_devices()

        self.input_combo.clear()
        for idx, name in inputs:
            self.input_combo.addItem(f"{idx}: {name}", idx)
        saved_in = self.config.get("input_device")
        if saved_in is not None:
            pos = self.input_combo.findData(saved_in)
            if pos >= 0:
                self.input_combo.setCurrentIndex(pos)

        self.output_combo.clear()
        for idx, name in outputs:
            self.output_combo.addItem(f"{idx}: {name}", idx)
        saved_out = self.config.get("output_device")
        if saved_out is not None:
            pos = self.output_combo.findData(saved_out)
            if pos >= 0:
                self.output_combo.setCurrentIndex(pos)

    def _rebuild_tts_backend(self):
        backend_name = self.backend_combo.currentText()
        try:
            if backend_name == "sapi5":
                self.tts_backend = SAPI5Backend()
                self.voice_combo.clear()
                self.voice_combo.addItems(self.tts_backend.list_voices())
                saved_voice = self.config.get("sapi5_voice", "")
                if saved_voice:
                    pos = self.voice_combo.findText(saved_voice)
                    if pos >= 0:
                        self.voice_combo.setCurrentIndex(pos)
            else:
                self.tts_backend = PiperBackend(
                    piper_exe=self.config.get("piper_exe", "piper"),
                    model_paths={
                        "ru": self.config.get("piper_model_ru", ""),
                        "en": self.config.get("piper_model_en", ""),
                    },
                )
                self.voice_combo.clear()
        except Exception as e:
            self._log("error", f"TTS backend '{backend_name}' unavailable: {e}", "")

    def _on_backend_changed(self, _text):
        self._rebuild_tts_backend()

    def _current_output_device_name(self):
        return self.output_combo.currentText().split(": ", 1)[-1] if self.output_combo.count() else None

    def _current_output_device_index(self):
        return self.output_combo.currentData()

    def speak(self, text, lang):
        """Single entry point for TTS, used by both the pipeline and macros."""
        if self.tts_backend is None:
            self._log("error", "TTS backend not initialized", lang)
            return
        if isinstance(self.tts_backend, SAPI5Backend):
            voice_name = self.voice_combo.currentText()
            if voice_name:
                self.tts_backend.set_voice(voice_name)
            self.tts_backend.set_rate(self.rate_slider.value())
            self.tts_backend.set_volume(self.volume_slider.value())
            self.tts_backend.speak_to_device(
                text, lang, output_device_name=self._current_output_device_name()
            )
        else:
            self.tts_backend.speak_to_device(
                text, lang, output_device=self._current_output_device_index()
            )
        self.last_spoken = (text, lang)

    def _test_voice(self):
        phrase = {"ru": "Проверка связи, приём.", "en": "Radio check, over."}
        mode = self.mode_combo.currentText()
        lang = MODES_TO_TEST_LANG.get(mode, "ru")
        try:
            self.speak(phrase.get(lang, phrase["ru"]), lang)
        except Exception as e:
            self._log("error", f"TEST VOICE: {e}", lang)

    # ---------- Pipeline ----------

    def _toggle_pipeline(self):
        if self.pipeline is None:
            mode = self.mode_combo.currentText()
            input_device = self.input_combo.currentData()
            self.pipeline = VoicePipeline(
                mode=mode,
                input_device=input_device,
                tts_speak_fn=self.speak,
                on_state=self._set_status_threadsafe,
                on_log=self._log_threadsafe,
                whisper_model_size=self.whisper_combo.currentText(),
            )
            self.pipeline.start()
            self.start_stop_btn.setText("STOP")
        else:
            self.pipeline.stop()
            self.pipeline = None
            self.start_stop_btn.setText("START")
            self._set_status(PipelineState.IDLE)

    def _set_status_threadsafe(self, state):
        QtCore.QMetaObject.invokeMethod(
            self, "_set_status_slot", QtCore.Qt.QueuedConnection, QtCore.Q_ARG(str, state)
        )

    @QtCore.Slot(str)
    def _set_status_slot(self, state):
        self._set_status(state)

    def _set_status(self, state):
        colors = {
            PipelineState.IDLE: "#4a5568",
            PipelineState.LOADING: "#5b7fff",
            PipelineState.LISTENING: "#2ecc71",
            PipelineState.PROCESSING: "#f2c94c",
            PipelineState.SPEAKING: "#ff8a1f",
        }
        self.status_dot.setStyleSheet(f"color: {colors.get(state, '#4a5568')}; font-size: 14px;")
        self.status_text.setText(f"SYSTEM STATUS: {state}")

    def _log_threadsafe(self, kind, text, lang):
        QtCore.QMetaObject.invokeMethod(
            self, "_log_slot", QtCore.Qt.QueuedConnection,
            QtCore.Q_ARG(str, kind), QtCore.Q_ARG(str, text), QtCore.Q_ARG(str, lang),
        )

    @QtCore.Slot(str, str, str)
    def _log_slot(self, kind, text, lang):
        self._log(kind, text, lang)

    def _log(self, kind, text, lang):
        prefix = {"stt": "STT", "translate": "TR", "error": "ERR"}.get(kind, kind.upper())
        self.log_view.append(f"[{prefix}/{lang}] {text}")

    # ---------- Macros ----------

    def _reload_macro_table(self):
        self.macro_table.setRowCount(0)
        for macro in self.macros:
            self._append_macro_row(macro.get("hotkey", ""), macro.get("phrase", ""))

    def _append_macro_row(self, hotkey="", phrase=""):
        row = self.macro_table.rowCount()
        self.macro_table.insertRow(row)
        self.macro_table.setItem(row, 0, QtWidgets.QTableWidgetItem(hotkey))
        self.macro_table.setItem(row, 1, QtWidgets.QTableWidgetItem(phrase))

    def _add_macro_row(self):
        self._append_macro_row("f9", "New phrase")

    def _remove_macro_row(self):
        row = self.macro_table.currentRow()
        if row >= 0:
            self.macro_table.removeRow(row)

    def _collect_macros_from_table(self):
        macros = []
        for row in range(self.macro_table.rowCount()):
            hotkey_item = self.macro_table.item(row, 0)
            phrase_item = self.macro_table.item(row, 1)
            hotkey = hotkey_item.text().strip() if hotkey_item else ""
            phrase = phrase_item.text().strip() if phrase_item else ""
            if hotkey and phrase:
                macros.append({"hotkey": hotkey, "phrase": phrase})
        return macros

    def _save_macros(self):
        self.macros = self._collect_macros_from_table()
        save_json(MACROS_PATH, self.macros)
        self._register_all_hotkeys()
        self._log("stt", "Macros saved, hotkeys re-registered", "-")

    def _register_all_hotkeys(self):
        try:
            import keyboard
        except ImportError:
            self._log("error", "'keyboard' library not installed, hotkeys disabled", "-")
            return

        for hotkey in self._registered_hotkeys:
            try:
                keyboard.remove_hotkey(hotkey)
            except Exception:
                pass
        self._registered_hotkeys = []

        if self._repeat_hotkey_registered:
            try:
                keyboard.remove_hotkey(self._repeat_hotkey_registered)
            except Exception:
                pass
            self._repeat_hotkey_registered = None

        for macro in self.macros:
            hotkey = macro.get("hotkey")
            if not hotkey:
                continue
            keyboard.add_hotkey(
                hotkey, lambda hk=hotkey: self.hotkey_signal.triggered.emit(hk)
            )
            self._registered_hotkeys.append(hotkey)

        repeat_hotkey = self.repeat_hotkey_edit.text().strip()
        if repeat_hotkey:
            keyboard.add_hotkey(
                repeat_hotkey, lambda: self.hotkey_signal.repeat_triggered.emit()
            )
            self._repeat_hotkey_registered = repeat_hotkey

    def _play_macro_by_hotkey(self, hotkey):
        for macro in self.macros:
            if macro.get("hotkey") == hotkey:
                mode = self.mode_combo.currentText()
                target_lang = MODES_TO_TEST_LANG.get(mode, "ru")
                phrase = macro["phrase"]
                source_lang = detect_text_lang(phrase)
                text_to_speak, lang_to_speak = phrase, source_lang
                if target_lang != source_lang:
                    try:
                        translation = get_translator(source_lang, target_lang)
                        text_to_speak = translation.translate(phrase)
                        lang_to_speak = target_lang
                        self._log("translate", text_to_speak, target_lang)
                    except Exception as e:
                        self._log("error", f"[MACRO {hotkey}] translate error: {e}", source_lang)
                try:
                    self.speak(text_to_speak, lang_to_speak)
                    self._log("stt", f"[MACRO {hotkey}] {text_to_speak}", lang_to_speak)
                except Exception as e:
                    self._log("error", f"[MACRO {hotkey}] TTS error: {e}", lang_to_speak)
                break

    def _repeat_last(self):
        if not self.last_spoken:
            self._log("error", "Nothing to repeat yet", "-")
            return
        text, lang = self.last_spoken
        try:
            self.speak(text, lang)
            self._log("stt", f"[REPEAT] {text}", lang)
        except Exception as e:
            self._log("error", f"[REPEAT] TTS error: {e}", lang)

    # ---------- Shutdown ----------

    def closeEvent(self, event):
        if self.pipeline is not None:
            self.pipeline.stop()
        self.config.update({
            "mode": self.mode_combo.currentText(),
            "whisper_model": self.whisper_combo.currentText(),
            "input_device": self.input_combo.currentData(),
            "output_device": self.output_combo.currentData(),
            "tts_backend": self.backend_combo.currentText(),
            "sapi5_voice": self.voice_combo.currentText(),
            "rate": self.rate_slider.value(),
            "volume": self.volume_slider.value(),
            "repeat_hotkey": self.repeat_hotkey_edit.text().strip(),
        })
        save_json(CONFIG_PATH, self.config)
        save_json(MACROS_PATH, self._collect_macros_from_table())
        super().closeEvent(event)


MODES_TO_TEST_LANG = {
    "RU-RU": "ru", "RU-EN": "en", "EN-EN": "en", "EN-RU": "ru", "AUTO": "ru",
}


def detect_text_lang(text):
    """Cheap heuristic: any Cyrillic character means Russian, otherwise English."""
    return "ru" if any("\u0400" <= ch <= "\u04ff" for ch in text) else "en"


def main():
    app = QtWidgets.QApplication(sys.argv)
    app.setStyleSheet(STYLE)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
