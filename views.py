from django.http import JsonResponse
from django.views import View
import requests
import telebot
from telebot import types
import threading
import time
import os
from dotenv import load_dotenv
from bot.models import Device
from collections import defaultdict
import django
from django.conf import settings
from users.utils import save_telegram_user, save_users_locations
from BotAnalytics.views import log_command_decorator, save_selected_device_to_db

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)

def get_device_data():
    url = "https://climatenet.am/device_inner/list/"
    try:
        response = requests.get(url)
        response.raise_for_status()  
        devices = response.json()
        locations = defaultdict(list)
        device_ids = {}
        for device in devices:
            device_ids[device["name"]] = device["generated_id"]
            locations[device.get("parent_name", "Unknown")].append(device["name"])
        return locations, device_ids
    except requests.RequestException as e:
        print(f"Error fetching device data: {e}")
        return {}, {}

locations, device_ids = get_device_data()
user_context = {}

devices_with_issues = ["Berd", "Ashotsk", "Gavar", "Artsvaberd", 
                       "Chambarak", "Areni", "Amasia"]

def fetch_latest_measurement(device_id):
    url = f"https://climatenet.am/device_inner/{device_id}/latest/"
    response = requests.get(url)
    if response.status_code == 200:
        data = response.json()
        if data:
            latest_measurement = data[0]  
            timestamp = latest_measurement["time"].replace("T", " ")
            return {
                "timestamp": timestamp,
                "uv": latest_measurement.get("uv"),
                "lux": latest_measurement.get("lux"),
                "temperature": latest_measurement.get("temperature"),
                "pressure": latest_measurement.get("pressure"),
                "humidity": latest_measurement.get("humidity"),
                "pm1": latest_measurement.get("pm1"),
                "pm2_5": latest_measurement.get("pm2_5"),
                "pm10": latest_measurement.get("pm10"),
                "wind_speed": latest_measurement.get("speed"),
                "rain": latest_measurement.get("rain"),
                "wind_direction": latest_measurement.get("wind_direction")
            }
        else:
            return None
    else:
        print(f"Failed to fetch data: {response.status_code}")
        return None

def start_bot():
    bot.polling(none_stop=True)

def run_bot():
    while True:
        try:
            start_bot()
        except Exception as e:
            print(f"Error occurred: {e}")
            time.sleep(15)

def start_bot_thread():
    bot_thread = threading.Thread(target=run_bot)
    bot_thread.start()

def send_location_selection(chat_id, message_text='Please choose a location: 📍'):
    location_markup = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    for country in locations.keys():
        location_markup.add(types.KeyboardButton(country))
    bot.send_message(chat_id, message_text, reply_markup=location_markup)

@bot.message_handler(commands=['start'])
@log_command_decorator
def start(message):
    bot.send_message(
        message.chat.id,
        '🌤️ Welcome to ClimateNet! 🌧️'
    )
    save_telegram_user(message.from_user)
    bot.send_message(
        message.chat.id,
        f'''Hello {message.from_user.first_name}! 👋 I am your personal climate assistant. 
With me, you can: 
    🔹 Access current measurements of temperature, humidity, wind speed, and more, refreshed every 15 minutes.
    🔹 Compare weather data between any two devices (e.g., TUMO in Yerevan vs. a device in Gyumri).
'''
    )
    send_location_selection(message.chat.id)

@bot.message_handler(func=lambda message: message.text in locations.keys())
@log_command_decorator
def handle_country_selection(message):
    selected_country = message.text
    chat_id = message.chat.id
    if chat_id not in user_context:
        user_context[chat_id] = {}

    if user_context[chat_id].get('comparing'):
        if 'device1' not in user_context[chat_id]:
            user_context[chat_id]['region1'] = selected_country
        else:
            user_context[chat_id]['region2'] = selected_country
    else:
        user_context[chat_id]['selected_country'] = selected_country

    markup = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    for device in locations[selected_country]:
        markup.add(types.KeyboardButton(device))
    if user_context[chat_id].get('comparing'):
        markup.add(types.KeyboardButton('/Cancel'))
    else:
        markup.add(types.KeyboardButton('/Change_location'))
    bot.send_message(chat_id, 'Please choose a device: ✅', reply_markup=markup)

def uv_index(uv):
    if uv is None:
        return " "
    if uv < 3:
        return "Low 🟢"
    elif 3 <= uv <= 5:
        return "Moderate 🟡"
    elif 6 <= uv <= 7:
        return "High 🟠"
    elif 8 <= uv <= 10:
        return "Very High 🔴"
    else:
        return "Extreme 🟣"

def pm_level(pm, pollutant):
    if pm is None:
        return "N/A"
    thresholds = {
        "PM1.0": [50, 100, 150, 200, 300],
        "PM2.5": [12, 36, 56, 151, 251],
        "PM10": [54, 154, 254, 354, 504]
    }
    levels = [
        "Good 🟢",
        "Moderate 🟡",
        "Unhealthy for Sensitive Groups 🟠",
        "Unhealthy 🟠",
        "Very Unhealthy 🔴",
        "Hazardous 🔴"
    ]
    thresholds = thresholds.get(pollutant, [])
    for i, limit in enumerate(thresholds):
        if pm <= limit:
            return levels[i]
    return levels[-1]

import math

def get_formatted_data(measurement, selected_device):
    def safe_value(value, is_round=False):
        if value is None or (isinstance(value, float) and math.isnan(value)):
            return "NA"
        return round(value) if is_round else value
    
    uv_description = uv_index(measurement.get('uv'))
    pm1_description = pm_level(measurement.get('pm1'), "PM1.0")
    pm2_5_description = pm_level(measurement.get('pm2_5'), "PM2.5")
    pm10_description = pm_level(measurement.get('pm10'), "PM10")
    
    if selected_device in devices_with_issues:
        technical_issues_message = "\n⚠️ Note: At this moment this device has technical issues."
    else:
        technical_issues_message = ""

    return (
        f"<b>𝗟𝗮𝘁𝗲𝘀𝘁 𝗠𝗲𝗮𝘀𝘂𝗿𝗲𝗺𝗲𝗻𝘁</b>\n"
        f"🔹 <b>Device:</b> <b>{selected_device}</b>\n"
        f"🔹 <b>Timestamp:</b> {safe_value(measurement.get('timestamp'))}\n\n"
        f"<b> 𝗟𝗶𝗴𝗵𝘁 𝗮𝗻𝗱 𝗨𝗩 𝗜𝗻𝗳𝗼𝗿𝗺𝗮𝘁𝗶𝗼𝗻</b>\n"
        f"☀️ <b>UV Index:</b> {safe_value(measurement.get('uv'))} ({uv_description})\n"
        f"🔆 <b>Light Intensity:</b> {safe_value(measurement.get('lux'))} lux\n\n"
        f"<b> 𝗘𝗻𝘃𝗶𝗿𝗼𝗻𝗺𝗲𝗻𝘁𝗮𝗹 𝗖𝗼𝗻𝗱𝗶𝘁𝗶𝗼𝗻𝘀</b>\n"
        f"🌡️ <b>Temperature:</b> {safe_value(measurement.get('temperature'), is_round=True)}°C\n"
        f"⏲️ <b>Atmospheric Pressure:</b> {safe_value(measurement.get('pressure'))} hPa\n"
        f"💧 <b>Humidity:</b> {safe_value(measurement.get('humidity'))}%\n\n"
        f"<b> 𝗔𝗶𝗿 𝗤𝘂𝗮𝗹𝗶𝘁𝘆 𝗟𝗲𝘃𝗲𝗹𝘀</b>\n"
        f"🫁 <b>PM1.0:</b> {safe_value(measurement.get('pm1'))} µg/m³  ({pm1_description})\n"
        f"💨 <b>PM2.5:</b> {safe_value(measurement.get('pm2_5'))} µg/m³ ({pm2_5_description})\n"
        f"🌫️ <b>PM10:</b> {safe_value(measurement.get('pm10'))} µg/m³ ({pm10_description})\n\n"
        f"<b>𝗪𝗲𝗮𝘁𝗵𝗲𝗿 𝗖𝗼𝗻𝗱𝗶𝘁𝗶𝗼𝗻 </b>\n"
        f"🌪️ <b>Wind Speed:</b> {safe_value(measurement.get('wind_speed'))} m/s\n"
        f"🌧️ <b>Rainfall:</b> {safe_value(measurement.get('rain'))} mm\n"
        f"🧭 <b>Wind Direction:</b> {safe_value(measurement.get('wind_direction'))}\n\n"
        f"🔍 <b>Detected Weather Condition:</b> {detect_weather_condition(measurement, for_comparison=False)}\n"
        f"{technical_issues_message}"
    )

def format_comparison(device1, device1_data, device2, device2_data):
    def safe_value(value, is_round=False):
        if value is None or (isinstance(value, float) and math.isnan(value)):
            return "N/A"
        return round(value) if is_round else value

    def compare_values(val1, val2, unit, desc, reverse=False):
        if val1 == "N/A" or val2 == "N/A":
            return f"{val1} {unit} vs {val2} {unit} (N/A)"
        val1, val2 = float(val1), float(val2)
        if val1 > val2:
            return f"{val1} {unit} vs {val2} {unit} ({device1} is {desc})"
        elif val2 > val1:
            return f"{val1} {unit} vs {val2} {unit} ({device2} is {desc})"
        else:
            return f"{val1} {unit} vs {val2} {unit} (Equal)"

    uv1 = safe_value(device1_data.get('uv'))
    uv2 = safe_value(device2_data.get('uv'))
    lux1 = safe_value(device1_data.get('lux'))
    lux2 = safe_value(device2_data.get('lux'))
    temp1 = safe_value(device1_data.get('temperature'), is_round=True)
    temp2 = safe_value(device2_data.get('temperature'), is_round=True)
    pressure1 = safe_value(device1_data.get('pressure'))
    pressure2 = safe_value(device2_data.get('pressure'))
    humidity1 = safe_value(device1_data.get('humidity'))
    humidity2 = safe_value(device2_data.get('humidity'))
    pm1_1 = safe_value(device1_data.get('pm1'))
    pm1_2 = safe_value(device2_data.get('pm1'))
    pm2_5_1 = safe_value(device2_data.get('pm2_5'))
    pm2_5_2 = safe_value(device2_data.get('pm2_5'))
    pm10_1 = safe_value(device1_data.get('pm10'))
    pm10_2 = safe_value(device2_data.get('pm10'))
    wind_speed1 = safe_value(device1_data.get('wind_speed'))
    wind_speed2 = safe_value(device2_data.get('wind_speed'))
    rain1 = safe_value(device1_data.get('rain'))
    rain2 = safe_value(device2_data.get('rain'))
    wind_dir1 = safe_value(device1_data.get('wind_direction'))
    wind_dir2 = safe_value(device2_data.get('wind_direction'))

    uv_desc1 = uv_index(device1_data.get('uv'))
    uv_desc2 = uv_index(device2_data.get('uv'))
    pm1_desc1 = pm_level(device1_data.get('pm1'), "PM1.0")
    pm1_desc2 = pm_level(device2_data.get('pm1'), "PM1.0")
    pm2_5_desc1 = pm_level(device1_data.get('pm2_5'), "PM2.5")
    pm2_5_desc2 = pm_level(device2_data.get('pm2_5'), "PM2.5")
    pm10_desc1 = pm_level(device1_data.get('pm10'), "PM10")
    pm10_desc2 = pm_level(device2_data.get('pm10'), "PM10")

    weather1 = detect_weather_condition(device1_data, for_comparison=True)
    weather2 = detect_weather_condition(device2_data, for_comparison=True)
    weather_line = f"🔍 <b>Detected Weather Condition:</b> {weather1} vs {weather2}\n" if weather1 or weather2 else ""

    summary = []
    if temp1 != "N/A" and temp2 != "N/A" and float(temp1) > float(temp2):
        summary.append(f"{device1} is warmer")
    elif temp2 != "N/A" and temp1 != "N/A" and float(temp2) > float(temp1):
        summary.append(f"{device2} is warmer")
    if uv1 != "N/A" and uv2 != "N/A" and float(uv1) > float(uv2):
        summary.append(f"{device1} is sunnier")
    elif uv2 != "N/A" and uv1 != "N/A" and float(uv2) > float(uv1):
        summary.append(f"{device2} is sunnier")
    if pm2_5_1 != "N/A" and pm2_5_2 != "N/A" and float(pm2_5_1) < float(pm2_5_2):
        summary.append(f"{device1} has cleaner air")
    elif pm2_5_2 != "N/A" and pm2_5_1 != "N/A" and float(pm2_5_2) < float(pm2_5_1):
        summary.append(f"{device2} has cleaner air")
    summary_text = f"🔹 <b>Summary:</b> {', '.join(summary) or 'Conditions are similar!'}\n"

    return (
        f"<b>𝗪𝗲𝗮𝘁𝗵𝗲𝗿 𝗖𝗼𝗺𝗽𝗮𝗿𝗶𝘀𝗼𝗻</b>\n"
        f"🔹 <b>Devices:</b> <b>{device1}</b> vs <b>{device2}</b>\n"
        f"🔹 <b>Timestamp:</b> {device1_data.get('timestamp')}\n\n"
        f"<b> 𝗟𝗶𝗴𝗵𝘁 𝗮𝗻𝗱 𝗨𝗩 𝗜𝗻𝗳𝗼𝗿𝗺𝗮𝘁𝗶𝗼𝗻</b>\n"
        f"☀️ <b>UV Index:</b> {compare_values(uv1, uv2, '', 'sunnier')} ({uv_desc1} vs {uv_desc2})\n"
        f"🔆 <b>Light Intensity:</b> {compare_values(lux1, lux2, 'lux', 'brighter')}\n\n"
        f"<b> 𝗘𝗻𝘃𝗶𝗿𝗼𝗻𝗺𝗲𝗻𝘁𝗮𝗹 𝗖𝗼𝗻𝗱𝗶𝘁𝗶𝗼𝗻𝘀</b>\n"
        f"🌡️ <b>Temperature:</b> {compare_values(temp1, temp2, '°C', 'warmer')}\n"
        f"⏲️ <b>Atmospheric Pressure:</b> {compare_values(pressure1, pressure2, 'hPa', 'higher')}\n"
        f"💧 <b>Humidity:</b> {compare_values(humidity1, humidity2, '%', 'more humid')}\n\n"
        f"<b> 𝗔𝗶𝗿 𝗤𝘂𝗮𝗹𝗶𝘁𝘆 𝗟𝗲𝘃𝗲𝗹𝘀</b>\n"
        f"🫁 <b>PM1.0:</b> {compare_values(pm1_1, pm1_2, 'µg/m³', 'cleaner', reverse=True)} ({pm1_desc1} vs {pm1_desc2})\n"
        f"💨 <b>PM2.5:</b> {compare_values(pm2_5_1, pm2_5_2, 'µg/m³', 'cleaner', reverse=True)} ({pm2_5_desc1} vs {pm2_5_desc2})\n"
        f"🌫️ <b>PM10:</b> {compare_values(pm10_1, pm10_2, 'µg/m³', 'cleaner', reverse=True)} ({pm10_desc1} vs {pm10_desc2})\n\n"
        f"<b>𝗪𝗲𝗮𝘁𝗵𝗲�_r 𝗖𝗼𝗻𝗱𝗶𝘁𝗶𝗼𝗻</b>\n"
        f"🌪️ <b>Wind Speed:</b> {compare_values(wind_speed1, wind_speed2, 'm/s', 'windier')}\n"
        f"🌧️ <b>Rainfall:</b> {compare_values(rain1, rain2, 'mm', 'wetter')}\n"
        f"🧭 <b>Wind Direction:</b> {wind_dir1} vs {wind_dir2}\n"
        f"{weather_line}\n"
        f"{summary_text}"
    )

@bot.message_handler(func=lambda message: message.text in [device for devices in locations.values() for device in devices])
@log_command_decorator
def handle_device_selection(message):
    selected_device = message.text
    chat_id = message.chat.id
    device_id = device_ids.get(selected_device)
    
    if not device_id:
        bot.send_message(chat_id, "⚠️ Device not found. ❌", reply_markup=get_command_menu())
        return

    if chat_id not in user_context:
        user_context[chat_id] = {'selected_country': None}

    if user_context[chat_id].get('comparing'):
        if 'device1' not in user_context[chat_id]:
            user_context[chat_id]['device1'] = selected_device
            user_context[chat_id]['device1_id'] = device_id
            send_location_selection(chat_id, f"Selected {selected_device} as first device. Choose the location for the second device: 📍")
        else:
            user_context[chat_id]['device2'] = selected_device
            user_context[chat_id]['device2_id'] = device_id
            compare_devices(chat_id)
            user_context[chat_id].pop('comparing', None)
            user_context[chat_id].pop('device1', None)
            user_context[chat_id].pop('device1_id', None)
            user_context[chat_id].pop('device2', None)
            user_context[chat_id].pop('device2_id', None)
            user_context[chat_id].pop('region1', None)
            user_context[chat_id].pop('region2', None)
    else:
        user_context[chat_id]['selected_device'] = selected_device
        user_context[chat_id]['device_id'] = device_id
        save_selected_device_to_db(user_id=message.from_user.id, context=user_context[chat_id], device_id=device_id)
        command_markup = get_command_menu(cur=selected_device)
        measurement = fetch_latest_measurement(device_id)
        if measurement:
            formatted_data = get_formatted_data(measurement=measurement, selected_device=selected_device)
            bot.send_message(chat_id, formatted_data, reply_markup=command_markup, parse_mode='HTML')
            bot.send_message(chat_id, '''For the next measurement, select\t
/Current 📍 every quarter of the hour. 🕒''')
        else:
            bot.send_message(chat_id, "⚠️ Error retrieving data. Please try again later.", reply_markup=command_markup)

def get_command_menu(cur=None):
    if cur is None:
        cur = ""
    command_markup = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    command_markup.add(
        types.KeyboardButton(f'/Current 📍{cur}'),
        types.KeyboardButton('/Compare 🔍'),
        types.KeyboardButton('/Change_device 🔄'),
        types.KeyboardButton('/Help ❓'),
        types.KeyboardButton('/Website 🌐'),
        types.KeyboardButton('/Map 🗺️'),
        types.KeyboardButton('/Share_location 🌍'),
    )
    return command_markup

def get_device_selection_markup(country):
    markup = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    for device in locations[country]:
        markup.add(types.KeyboardButton(device))
    markup.add(types.KeyboardButton('/Cancel'))
    return markup

@bot.message_handler(commands=['Compare'])
@log_command_decorator
def start_comparison(message):
    chat_id = message.chat.id
    if chat_id not in user_context:
        user_context[chat_id] = {}
    user_context[chat_id]['comparing'] = True
    user_context[chat_id].pop('device1', None)
    user_context[chat_id].pop('device1_id', None)
    user_context[chat_id].pop('device2', None)
    user_context[chat_id].pop('device2_id', None)
    user_context[chat_id].pop('region1', None)
    user_context[chat_id].pop('region2', None)
    send_location_selection(chat_id, 'Choose the location for the first device to compare: 📍')

def compare_devices(chat_id):
    device1 = user_context[chat_id]['device1']
    device1_id = user_context[chat_id]['device1_id']
    device2 = user_context[chat_id]['device2']
    device2_id = user_context[chat_id]['device2_id']
    
    measurement1 = fetch_latest_measurement(device1_id)
    measurement2 = fetch_latest_measurement(device2_id)
    
    command_markup = get_command_menu()
    if measurement1 and measurement2:
        comparison_data = format_comparison(device1, measurement1, device2, measurement2)
        bot.send_message(chat_id, comparison_data, reply_markup=command_markup, parse_mode='HTML')
    else:
        bot.send_message(chat_id, "⚠️ Error retrieving data for one or both devices. Please try again.", reply_markup=command_markup)

@bot.message_handler(commands=['Cancel'])
@log_command_decorator
def cancel_comparison(message):
    chat_id = message.chat.id
    if chat_id in user_context:
        user_context[chat_id].pop('comparing', None)
        user_context[chat_id].pop('device1', None)
        user_context[chat_id].pop('device1_id', None)
        user_context[chat_id].pop('device2', None)
        user_context[chat_id].pop('device2_id', None)
        user_context[chat_id].pop('region1', None)
        user_context[chat_id].pop('region2', None)
    bot.send_message(chat_id, "Comparison canceled. Back to main menu.", reply_markup=get_command_menu())

@bot.message_handler(commands=['Current'])
@log_command_decorator
def get_current_data(message):
    chat_id = message.chat.id
    command_markup = get_command_menu()
    save_telegram_user(message.from_user)
    if chat_id in user_context and 'device_id' in user_context[chat_id]:
        device_id = user_context[chat_id]['device_id']
        selected_device = user_context[chat_id].get('selected_device')
        command_markup = get_command_menu(cur=selected_device)
        measurement = fetch_latest_measurement(device_id)
        if measurement:
            formatted_data = get_formatted_data(measurement=measurement, selected_device=selected_device)
            bot.send_message(chat_id, formatted_data, reply_markup=command_markup, parse_mode='HTML')
            bot.send_message(chat_id, '''For the next measurement, select\t
/Current 📍 every quarter of the hour. 🕒''')
        else:
            bot.send_message(chat_id, "⚠️ Error retrieving data. Please try again later.", reply_markup=command_markup)
    else:
        bot.send_message(chat_id, "⚠️ Please select a device first using /Change_device 🔄.", reply_markup=command_markup)

@bot.message_handler(commands=['Help'])
@log_command_decorator
def help(message):
    bot.send_message(message.chat.id, '''
<b>/Current 📍:</b> Get the latest climate data in selected location.\n
<b>/Compare 🔍:</b> Compare weather data between two devices.\n
<b>/Change_device 🔄:</b> Change to another climate monitoring device.\n
<b>/Help ❓:</b> Show available commands.\n
<b>/Website 🌐:</b> Visit our website for more information.\n
<b>/Map 🗺️:</b> View the locations of all devices on a map.\n
<b>/Share_location 🌍:</b> Share your location.\n
''', parse_mode='HTML')

@bot.message_handler(commands=['Change_device'])
@log_command_decorator
def change_device(message):
    chat_id = message.chat.id
    if chat_id in user_context:
        user_context[chat_id].pop('selected_device', None)
        user_context[chat_id].pop('device_id', None)
        user_context[chat_id].pop('comparing', None)
        user_context[chat_id].pop('device1', None)
        user_context[chat_id].pop('device1_id', None)
        user_context[chat_id].pop('device2', None)
        user_context[chat_id].pop('device2_id', None)
        user_context[chat_id].pop('region1', None)
        user_context[chat_id].pop('region2', None)
    send_location_selection(chat_id)

@bot.message_handler(commands=['Change_location'])
@log_command_decorator
def change_location(message):
    chat_id = message.chat.id
    if chat_id in user_context:
        user_context[chat_id].pop('comparing', None)
        user_context[chat_id].pop('device1', None)
        user_context[chat_id].pop('device1_id', None)
        user_context[chat_id].pop('device2', None)
        user_context[chat_id].pop('device2_id', None)
        user_context[chat_id].pop('region1', None)
        user_context[chat_id].pop('region2', None)
    send_location_selection(chat_id)

@bot.message_handler(commands=['Website'])
@log_command_decorator
def website(message):
    markup = types.InlineKeyboardMarkup()
    button = types.InlineKeyboardButton('Visit Website', url='https://climatenet.am/en/')
    markup.add(button)
    bot.send_message(
        message.chat.id,
        'For more information, click the button below to visit our official website: 🖥️',
        reply_markup=markup
    )

@bot.message_handler(commands=['Map'])
@log_command_decorator
def map(message):
    chat_id = message.chat.id
    image = 'https://images-in-website.s3.us-east-1.amazonaws.com/Bot/map.png'
    bot.send_message(chat_id, 
'''📌 The highlighted locations indicate the current active climate devices. 🗺️ ''')
    bot.send_photo(chat_id, photo=image)

@bot.message_handler(content_types=['audio', 'document', 'photo', 'sticker', 'video', 'video_note', 'voice', 'contact', 'venue', 'animation'])
@log_command_decorator
def handle_media(message):
    bot.send_message(
        message.chat.id,
        '''❗ Please use a valid command.
You can see all available commands by typing /Help❓
''')

@bot.message_handler(func=lambda message: not message.text.startswith('/'))
@log_command_decorator
def handle_text(message):
    bot.send_message(
        message.chat.id,
        '''❗ Please use a valid command.
You can see all available commands by typing /Help❓
''')

@bot.message_handler(commands=['Share_location'])
@log_command_decorator
def request_location(message):
    location_button = types.KeyboardButton("📍 Share Location", request_location=True)
    markup = types.ReplyKeyboardMarkup(row_width=1, resize_keyboard=True, one_time_keyboard=True)
    back_to_menu_button = types.KeyboardButton("/back 🔙")
    markup.add(location_button, back_to_menu_button)
    bot.send_message(
        message.chat.id,
        "Click the button below to share your location 🔽",
        reply_markup=markup
    )

@bot.message_handler(commands=['back'])
@log_command_decorator
def go_back_to_menu(message):
    bot.send_message(
        message.chat.id,
        "You are back to the main menu. How can I assist you?",
        reply_markup=get_command_menu()
    )

@bot.message_handler(content_types=['location'])
@log_command_decorator
def handle_location(message):
    user_location = message.location
    if user_location:
        latitude = user_location.latitude
        longitude = user_location.longitude
        res = f"{longitude},{latitude}"
        save_users_locations(from_user=message.from_user.id, location=res)
        command_markup = get_command_menu()
        bot.send_message(
            message.chat.id,
            "Select other commands to continue ▶️",
            reply_markup=command_markup
        )
    else:
        bot.send_message(
            message.chat.id,
            "Failed to get your location. Please try again."
        )

def detect_weather_condition(measurement, for_comparison=False):
    temperature = measurement.get("temperature")
    humidity = measurement.get("humidity")
    lux = measurement.get("lux")
    pm2_5 = measurement.get("pm2_5")
    uv = measurement.get("uv")
    wind_speed = measurement.get("wind_speed")
    if temperature is not None and temperature < 1 and humidity and humidity > 85:
        return "Possibly Snowing ❄️"
    elif lux is not None and lux < 100 and humidity and humidity > 90 and pm2_5 and pm2_5 > 40:
        return "Foggy 🌫️"
    elif lux and lux < 300 and uv and uv < 2:
        return "Cloudy ☁️"
    elif lux and lux > 5 and uv and uv > 3:
        return "Sunny ☀️"
    else:
        return "" if for_comparison else "Nothing detected ❌"

if __name__ == "__main__":
    start_bot_thread()

def run_bot_view(request):
    start_bot_thread()
    return JsonResponse({'status': 'Bot is running in the background!'})
