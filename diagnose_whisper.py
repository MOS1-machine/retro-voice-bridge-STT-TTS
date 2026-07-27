"""
diagnose_whisper.py

Разовый диагностический скрипт: скачивает и загружает модель faster-whisper
с явным выводом прогресса, чтобы понять, где именно зависает загрузка
(скачивание файлов с Hugging Face, или инициализация ctranslate2).
Запускать: py -3.13 diagnose_whisper.py
"""

import time

print("Шаг 1: импортирую faster_whisper...")
t0 = time.time()
from faster_whisper import WhisperModel
print(f"  готово за {time.time()-t0:.1f} сек")

print("Шаг 2: скачиваю/загружаю модель 'small' (это может занять пару минут при первом запуске)...")
print("  если тут зависнет дольше 5 минут без изменений — дело в сети (см. подсказку ниже)")
t0 = time.time()
model = WhisperModel("small", device="cpu", compute_type="int8")
print(f"  готово за {time.time()-t0:.1f} сек")

print("Шаг 3: пробую распознать тишину (проверка что модель реально работает)...")
import numpy as np
silence = np.zeros(16000, dtype=np.float32)
segments, info = model.transcribe(silence, language="ru", beam_size=1)
text = "".join(s.text for s in segments)
print(f"  результат: '{text}' (пусто — это нормально, это была тишина)")

print("Всё готово, модель Whisper рабочая.")
