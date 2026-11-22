import math
import time
import pyvjoy
import serial
import struct
import ctypes
from datetime import datetime

# ============================================================
# 1. СПРАШИВАЕМ ПАРАМЕТРЫ ЗАПУСКА ("ХЛОПУШКА")
# ============================================================
game_name = input("🎮 Введи название игры: ").strip()
extra_param = input("⚙️  Введи доп. параметр (настройки/сценарий): ").strip()

# ============================================================
# 2. СОЗДАЁМ ЛОГ-ФАЙЛ
# ============================================================
timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
log_filename = f"ffb_log_{timestamp}.txt"
log_file = open(log_filename, "w", encoding="utf-8")

# Шапка файла
log_file.write("=" * 60 + "\n")
log_file.write(f"ЛОГ FFB\n")
log_file.write(f"Игра:        {game_name}\n")
log_file.write(f"Параметры:   {extra_param}\n")
log_file.write(f"Время старта:{timestamp}\n")
log_file.write("=" * 60 + "\n\n")
log_file.flush()

print(f"✅ Лог пишется в файл: {log_filename}")
print(f"🎮 Игра: {game_name}")
print(f"⚙️  Параметры: {extra_param}")

# ============================================================
# 3. НАСТРОЙКИ
# ============================================================
COM_PORT = 'COM7'
BAUD_RATE = 115200
START_BYTE = 0xAA
DATA_SIZE = 2

TARGET_FREQ_HZ = 500
INTERVAL_SEC = 1.0 / TARGET_FREQ_HZ

wheel_position = 0
ffb_value = 0.0  # ← ДОБАВЛЕНО: текущее значение FFB (-255..+255)

# Параметры обработки FFB
FFB_DEADZONE = 150        # Мёртвая зона (игнорируем значения < 150)
FFB_SMOOTHING = 0.3       # Коэффициент сглаживания (0.0..1.0)
last_ffb_value = 0.0      # Для сглаживания

# ============================================================
# 4. ПОДКЛЮЧЕНИЕ К vJoy
# ============================================================
j = pyvjoy.VJoyDevice(1)

# ============================================================
# 5. CALLBACK ДЛЯ FFB — пишем сырую инфу в файл + отправляем на Arduino
# ============================================================
def process_ffb_magnitude(magnitude):
    """Преобразование Magnitude (-10000..+10000) в диапазон -255..+255"""
    global last_ffb_value, ffb_value
    
    # Применяем мёртвую зону
    if abs(magnitude) < FFB_DEADZONE:
        scaled = 0.0
    else:
        # Масштабируем с учётом deadzone
        if magnitude > 0:
            scaled = ((magnitude - FFB_DEADZONE) / (10000 - FFB_DEADZONE)) 
        else:
            scaled = ((magnitude + FFB_DEADZONE) / (10000 - FFB_DEADZONE))
        
        # Ограничиваем диапазон
        scaled = max(-1.0, min(1.0, scaled))
    
    # Применяем экспоненциальное сглаживание
    smoothed = last_ffb_value + FFB_SMOOTHING * (scaled - last_ffb_value)
    last_ffb_value = smoothed
    print(f'{smoothed:.2f}')
    ffb_value = smoothed

def ffbcb(data, reptype):
    global ffb_value
    try:
        packetdict, ebi = pyvjoy.FFB_Effect_Manager.ffb_packet_to_dict(data, reptype)
        now = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        line = f"[{now}] reptype={reptype} | ebi={ebi} | data={packetdict}\n"
        log_file.write(line)
        log_file.flush()
        
        # ← ДОБАВЛЕНО: Обработка FFB для отправки на Arduino
        if reptype == 5:  # Обновление силы (Magnitude)
            try:
                magnitude = packetdict['const']['Magnitude']
                process_ffb_magnitude(magnitude)
            except (KeyError, TypeError):
                pass  # Если нет поля Magnitude, игнорируем
                
    except Exception as e:
        log_file.write(f"[ОШИБКА ПАРСИНГА] {e}\n")
        log_file.flush()

# Регистрируем callback
if j.ffb_supported():
    j.ffb_register_callback(ffbcb)
    print("✅ FFB callback зарегистрирован")
else:
    print("❌ FFB не поддерживается устройством vJoy!")

# ============================================================
# 6. ПОДКЛЮЧЕНИЕ К ARDUINO (Serial)
# ============================================================
try:
    ser = serial.Serial(COM_PORT, BAUD_RATE, timeout=1)
    print(f"✅ Подключено к {COM_PORT} на скорости {BAUD_RATE} бод")
except serial.SerialException as e:
    print(f"❌ Ошибка подключения к {COM_PORT}: {e}")
    log_file.close()
    exit()

buffer = bytearray()

# ============================================================
# 7. ФУНКЦИИ
# ============================================================
def transmit_ffb(ffb_val):
    """Отправка FFB значения (-255..+255) на Arduino"""
    packed_data = struct.pack('<f', ffb_val)
    message = bytearray([START_BYTE]) + packed_data
    ser.write(message)

def read_wheel_position():
    """Чтение позиции руля с Arduino и отправка в vJoy"""
    global wheel_position, buffer
    if ser.in_waiting > 0:
        new_bytes = ser.read(ser.in_waiting)
        buffer.extend(new_bytes)

        while len(buffer) > 0:
            if buffer[0] == START_BYTE:
                if len(buffer) >= 1 + DATA_SIZE:
                    data_bytes = buffer[1 : 1 + DATA_SIZE]
                    buffer = buffer[1 + DATA_SIZE :]

                    try:
                        position_value = 0x8000 - struct.unpack('<h', data_bytes)[0]
                        j.set_axis(pyvjoy.HID_USAGE_X, position_value)
                    except struct.error:
                        print(f"Ошибка при распаковке данных: {data_bytes}")
                    break
                else:
                    break
            else:
                buffer.pop(0)

# ============================================================
# 8. ОСНОВНОЙ ЦИКЛ
# ============================================================
last_send_time = time.time()

print("\n🚀 Работаю. Ctrl+C для выхода.\n")

if __name__ == "__main__":
    try:
        while True:
            current_time = time.time()

            # Читаем позицию руля с Arduino
            read_wheel_position()

            # ← ИЗМЕНЕНО: Отправляем FFB на Arduino с нужной частотой
            if (current_time - last_send_time) >= INTERVAL_SEC:
                transmit_ffb(ffb_value)  # ← Было: transmit(value_to_send)
                last_send_time = current_time

            time.sleep(0.0002)

    except KeyboardInterrupt:
        print("\n\n🛑 Выход по запросу пользователя (Ctrl+C).")
        log_file.write(f"\n=== Лог завершён: {datetime.now()} ===\n")
        log_file.close()
        print(f"✅ Файл сохранён: {log_filename}")
    finally:
        ser.close()
        print(f"✅ Порт {COM_PORT} закрыт.")
