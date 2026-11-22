import math
import time
import pyvjoy
import serial
import struct
import ctypes

# --- Настройки ---
COM_PORT = 'COM7'
BAUD_RATE = 115200
START_BYTE = 0xAA  # Произвольный стартовый байт (должен совпадать с Arduino)
DATA_SIZE = 2      # Размер данных (2 байта для int16_t)

wheel_position = 0
value_to_send = 0

j = pyvjoy.VJoyDevice(1)


def ffbcb(data, reptype):
    print("="*50)
    print(f"reptype: {reptype}")
    print(f"type(data): {type(data)}")
    
    # 1. Пробуем вытащить сырые байты из ctypes-структуры
    raw_hex = None
    try:
        if isinstance(data, ctypes.Structure):
            raw_bytes = bytes(ctypes.string_at(ctypes.byref(data), ctypes.sizeof(data)))
            raw_hex = raw_bytes.hex()
            print(f"RAW [{len(raw_bytes)} bytes]: {raw_hex}")
        elif hasattr(data, 'contents') and isinstance(data.contents, ctypes.Structure):
            # если пришёл указатель на структуру
            raw_bytes = bytes(ctypes.string_at(data, ctypes.sizeof(data.contents)))
            raw_hex = raw_bytes.hex()
            print(f"RAW [{len(raw_bytes)} bytes]: {raw_hex}")
        else:
            print("Не понял, как получить байты из data")
    except Exception as e:
        print(f"Ошибка получения raw bytes: {e}")

    # 2. Выводим все поля структуры, если они есть
    if hasattr(data, '_fields_'):
        print("--- Поля структуры ---")
        for field_name, field_type in data._fields_:
            try:
                value = getattr(data, field_name)
                print(f"  {field_name}: {value}")
            except Exception as e:
                print(f"  {field_name}: <ошибка чтения: {e}>")
    
    # 3. Парсим библиотекой (как ты и делал)
    try:
        packetdict, ebi = pyvjoy.FFB_Effect_Manager.ffb_packet_to_dict(data, reptype)
        print("PARSED:", packetdict, "ebi:", ebi)
    except Exception as e:
        print(f"Ошибка парсинга: {e}")

j.ffb_register_callback(ffbcb)

# --- Настройки ---
PRINT_INTERVAL = 0.4# Интервал для печати (10 Гц = 1 раз в 0.1 секунду)
AXIS_UPDATE_DELAY = 0.04

TARGET_FREQ_HZ = 500  # Целевая частота обновления
INTERVAL_SEC = 1.0 / TARGET_FREQ_HZ  # Интервал между отправками (1/500 = 0.002 секунды)

# --- Подключение к порту ---
try:
    ser = serial.Serial(COM_PORT, BAUD_RATE, timeout=1)
    print(f"Подключено к {COM_PORT} на скорости {BAUD_RATE} бод")
    print("Ожидание данных о позиции...")
except serial.SerialException as e:
    print(f"Ошибка подключения к {COM_PORT}: {e}")
    exit()

buffer = bytearray() # Буфер должен быть глобальным или передаваться в функцию

##data = [0, 0, 0]  # throttle, brake, calculated (J-L), turn_right
last_print_time = time.time()
last_axis_update_time = time.time()
last_send_time = time.time()


def transmit(value_to_send):
            packed_data = struct.pack('<f', value_to_send)
            message = bytearray([START_BYTE]) + packed_data
            ser.write(message)

def read_wheel_position():
    global wheel_position, buffer
    if ser.in_waiting > 0:
        new_bytes = ser.read(ser.in_waiting)
        buffer.extend(new_bytes)

        # Ищем стартовый байт в буфере
        while len(buffer) > 0:
            if buffer[0] == START_BYTE:
                # Найден стартовый байт
                if len(buffer) >= 1 + DATA_SIZE: # Проверяем, достаточно ли байт для данных
                    # Извлекаем 2 байта данных
                    data_bytes = buffer[1 : 1 + DATA_SIZE]
                    buffer = buffer[1 + DATA_SIZE :] # Удаляем обработанное сообщение из буфера

                    # Распаковываем как signed int16 (little-endian)
                    try:
                        position_value = 0x8000 - struct.unpack('<h', data_bytes)[0] # '<h' = little-endian signed short (int16_t)
##                        wheel_position = position_value
                        j.set_axis(pyvjoy.HID_USAGE_X, position_value)
                        # print(f"Принята позиция: {position_value}")  # можно убрать или оставить для дебага
                    except struct.error:
                        print(f"Ошибка при распаковке данных: {data_bytes}")
                    break # Перейти к следующему байту в буфере
                else:
                    # Недостаточно байт для данных, ждём дальше
                    break
            else:
                # Первый байт не стартовый, удаляем его
                buffer.pop(0)

if __name__ == "__main__":
    try:
        while True:
            current_time = time.time()

            read_wheel_position()
        

            if (current_time - last_send_time) >= INTERVAL_SEC:
                transmit(value_to_send)
                last_send_time = current_time
                
            time.sleep(0.0002)

    except KeyboardInterrupt:
        print("\nВыход по запросу пользователя (Ctrl+C).")
    finally:
        ser.close()
        print(f"Порт {COM_PORT} закрыт.")
