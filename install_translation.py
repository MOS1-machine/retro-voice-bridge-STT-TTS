"""
install_translation.py

Разовый скрипт: скачивает и устанавливает офлайн-модели перевода
Argos Translate для пар ru->en и en->ru. Печатает прогресс на каждом шаге,
чтобы было видно, что происходит, а не гадать по трафику.
Запускать один раз: py -3.13 install_translation.py
"""

import argostranslate.package as pkg

print("Шаг 1 из 4: скачиваю список доступных языковых пакетов...")
pkg.update_package_index()
print("Готово: список получен.")

print("Шаг 2 из 4: ищу нужные пакеты (ru->en и en->ru)...")
available = pkg.get_available_packages()
wanted = [p for p in available if (p.from_code, p.to_code) in [("ru", "en"), ("en", "ru")]]

if not wanted:
    print("Не нашёл нужные пакеты в индексе. Возможно сервер Argos Translate недоступен.")
else:
    print(f"Найдено пакетов: {len(wanted)}")
    for i, p in enumerate(wanted, start=1):
        print(f"Шаг 3.{i}: скачиваю пакет {p.from_code} -> {p.to_code} ({p})...")
        path = p.download()
        print(f"  скачано, устанавливаю...")
        pkg.install_from_path(path)
        print(f"  готово: {p.from_code} -> {p.to_code} установлен.")

print("Шаг 4 из 4: проверяю установленные языки...")
import argostranslate.translate as at
installed = at.get_installed_languages()
print("Установленные языки:", [l.code for l in installed])
print("Всё готово.")
