# -*- coding: utf-8 -*-
import telebot
import subprocess
import os
import zipfile
import tempfile
import shutil
from telebot import types
import time
from datetime import datetime, timedelta
import psutil
import sqlite3
import json
import logging
import signal
import threading
import re
import sys
import atexit
import requests

# --- Flask Keep Alive ---
from flask import Flask
from threading import Thread

app = Flask('')

@app.route('/')
def home():
    return "I'am BUXXMODZ Python Bot Host"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_flask)
    t.daemon = True
    t.start()
    print("Flask Keep-Alive server started.")
# --- End Flask Keep Alive ---

# --- Configuration ---
TOKEN = "8931598579:AAHpV3y6b6gWQSt2kj9o4cnyXZlz1iDIOKE"    # Replace with your actual token
OWNER_ID = 6697781634 # Replace with your Owner ID
ADMIN_ID = 6697781634 # Replace with your Admin ID (can be same as Owner)
YOUR_USERNAME = 'BUXXMODZZZZ' # Replace with your Telegram username (without the @)
UPDATE_CHANNEL = 'https://t.me/buxxprivate' # Default fallback update channel link

# Folder setup
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
UPLOAD_BOTS_DIR = os.path.join(BASE_DIR, 'upload_bots')
IROTECH_DIR = os.path.join(BASE_DIR, 'inf')
DATABASE_PATH = os.path.join(IROTECH_DIR, 'bot_data.db')

os.makedirs(UPLOAD_BOTS_DIR, exist_ok=True)
os.makedirs(IROTECH_DIR, exist_ok=True)

owner_remove_mode = {}  # Store user IDs who are in remove mode
user_upload_state = {}  # {user_id: {'category': 'python', 'mode': 'file'/'folder'}}

# File upload limits
FREE_USER_LIMIT = 5
SUBSCRIBED_USER_LIMIT = 15
ADMIN_LIMIT = 50
OWNER_LIMIT = float('inf')

# Initialize bot
bot = telebot.TeleBot(TOKEN)

# --- Data structures ---
bot_scripts = {} # {script_key: info_dict}
user_subscriptions = {} # {user_id: {'expiry': datetime_object_or_'forever'}}
user_files = {} # {user_id: [(file_name, file_type), ...]}
active_users = set()
admin_ids = {ADMIN_ID, OWNER_ID}
force_channels = {} # {chat_id_or_username: invite_link}
bot_locked = False

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Command Button Layouts (Upload Files Placed at the VERY TOP) ---
COMMAND_BUTTONS_LAYOUT_USER_SPEC = [
    ["📤 Upload File", "📂 Check Files"],
    ["⚡ Bot Speed", "📊 Statistics"],
    ["📢 Updates Channel", "📞 Contact Owner"]
]
ADMIN_COMMAND_BUTTONS_LAYOUT_USER_SPEC = [
    ["📤 Upload File", "📂 Check Files"],
    ["⚡ Bot Speed", "📊 Statistics"],
    ["💳 Subscriptions", "📢 Broadcast"],
    ["🔒 Lock Bot", "🟢 Running All Code"],
    ["👑 Admin Panel", "📢 Updates Channel"],
    ["📞 Contact Owner"]
]

# --- Database Setup ---
def init_db():
    """Initialize the database with required tables"""
    logger.info(f"Initializing database at: {DATABASE_PATH}")
    try:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS subscriptions
                     (user_id INTEGER PRIMARY KEY, expiry TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS user_files
                     (user_id INTEGER, file_name TEXT, file_type TEXT,
                      PRIMARY KEY (user_id, file_name))''')
        c.execute('''CREATE TABLE IF NOT EXISTS active_users
                     (user_id INTEGER PRIMARY KEY)''')
        c.execute('''CREATE TABLE IF NOT EXISTS admins
                     (user_id INTEGER PRIMARY KEY)''')
        c.execute('''CREATE TABLE IF NOT EXISTS force_channels
                     (channel_id TEXT PRIMARY KEY, invite_link TEXT)''')
        c.execute('INSERT OR IGNORE INTO admins (user_id) VALUES (?)', (OWNER_ID,))
        if ADMIN_ID != OWNER_ID:
             c.execute('INSERT OR IGNORE INTO admins (user_id) VALUES (?)', (ADMIN_ID,))
        conn.commit()
        conn.close()
        logger.info("Database initialized successfully.")
    except Exception as e:
        logger.error(f"❌ Database initialization error: {e}", exc_info=True)

def load_data():
    """Load data from database into memory"""
    logger.info("Loading data from database...")
    try:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()

        # Load subscriptions (supports 'forever' or ISO datetime)
        c.execute('SELECT user_id, expiry FROM subscriptions')
        for user_id, expiry in c.fetchall():
            if expiry == 'forever':
                user_subscriptions[user_id] = {'expiry': 'forever'}
            else:
                try:
                    user_subscriptions[user_id] = {'expiry': datetime.fromisoformat(expiry)}
                except ValueError:
                    logger.warning(f"⚠️ Invalid expiry date format for user {user_id}: {expiry}. Skipping.")

        # Load user files
        c.execute('SELECT user_id, file_name, file_type FROM user_files')
        for user_id, file_name, file_type in c.fetchall():
            if user_id not in user_files:
                user_files[user_id] = []
            user_files[user_id].append((file_name, file_type))

        # Load active users
        c.execute('SELECT user_id FROM active_users')
        active_users.update(user_id for (user_id,) in c.fetchall())

        # Load admins
        c.execute('SELECT user_id FROM admins')
        admin_ids.update(user_id for (user_id,) in c.fetchall())

        # Load Force Join Channels
        c.execute('SELECT channel_id, invite_link FROM force_channels')
        for ch_id, inv_link in c.fetchall():
            force_channels[ch_id] = inv_link

        conn.close()
        logger.info(f"Data loaded: {len(active_users)} users, {len(user_subscriptions)} subscriptions, {len(force_channels)} force channels.")
    except Exception as e:
        logger.error(f"❌ Error loading data: {e}", exc_info=True)

init_db()
load_data()
# --- End Database Setup ---

# --- Force Join / Join to Unlock Helper Functions ---
def is_user_joined_all(user_id):
    """Check if user has joined all admin-configured force channels"""
    if user_id in admin_ids or not force_channels:
        return True, []
    missing_channels = []
    for ch_id, inv_link in list(force_channels.items()):
        try:
            chat_target = int(ch_id) if (ch_id.startswith('-') or ch_id.isdigit()) else ch_id
            member = bot.get_chat_member(chat_target, user_id)
            if member.status in ['left', 'kicked']:
                missing_channels.append((ch_id, inv_link))
        except Exception as e:
            logger.warning(f"Could not verify membership for {user_id} in {ch_id}: {e}")
            # If bot is not admin in that channel or invalid channel, skip blocking or require check
            continue
    return len(missing_channels) == 0, missing_channels

def send_force_join_prompt(chat_id, user_id, message_id_to_edit=None):
    """Send or edit the Join-to-Unlock screen with admin-controlled links"""
    joined, missing = is_user_joined_all(user_id)
    markup = types.InlineKeyboardMarkup(row_width=1)
    idx = 1
    channels_to_show = missing if missing else list(force_channels.items())
    for ch_id, inv_link in channels_to_show:
        markup.add(types.InlineKeyboardButton(f"📢 Join Channel #{idx}", url=inv_link))
        idx += 1
    markup.add(types.InlineKeyboardButton("🔓 Joined (Verify & Unlock)", callback_data="verify_join_unlock"))

    text = (
        "🔒 **ACCESS LOCKED — JOIN TO UNLOCK** 🔒\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "✨ To use this bot and host your Python scripts for free, "
        "you must join our official channel(s) below!\n\n"
        "👇 **Click the button(s) below to join, then tap 'Joined (Verify & Unlock)':**"
    )
    try:
        if message_id_to_edit:
            bot.edit_message_text(text, chat_id, message_id_to_edit, reply_markup=markup, parse_mode='Markdown')
        else:
            bot.send_message(chat_id, text, reply_markup=markup, parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Error sending force join message: {e}")

# --- Subscription & Helper Functions ---
def is_user_subscribed(user_id):
    """Returns True if user has an active or forever subscription"""
    if user_id not in user_subscriptions:
        return False
    expiry = user_subscriptions[user_id].get('expiry')
    if expiry == 'forever':
        return True
    if isinstance(expiry, datetime) and expiry > datetime.now():
        return True
    return False

def get_user_folder(user_id):
    user_folder = os.path.join(UPLOAD_BOTS_DIR, str(user_id))
    os.makedirs(user_folder, exist_ok=True)
    return user_folder

def get_user_file_limit(user_id):
    if user_id == OWNER_ID: return OWNER_LIMIT
    if user_id in admin_ids: return ADMIN_LIMIT
    if is_user_subscribed(user_id):
        return SUBSCRIBED_USER_LIMIT
    return FREE_USER_LIMIT

def get_user_file_count(user_id):
    return len(user_files.get(user_id, []))

def is_bot_running(script_owner_id, file_name):
    script_key = f"{script_owner_id}_{file_name}"
    script_info = bot_scripts.get(script_key)
    if script_info and script_info.get('process'):
        try:
            proc = psutil.Process(script_info['process'].pid)
            is_running = proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE
            if not is_running:
                if 'log_file' in script_info and hasattr(script_info['log_file'], 'close') and not script_info['log_file'].closed:
                    try: script_info['log_file'].close()
                    except Exception: pass
                if script_key in bot_scripts:
                    del bot_scripts[script_key]
            return is_running
        except psutil.NoSuchProcess:
            if 'log_file' in script_info and hasattr(script_info['log_file'], 'close') and not script_info['log_file'].closed:
                try: script_info['log_file'].close()
                except Exception: pass
            if script_key in bot_scripts:
                 del bot_scripts[script_key]
            return False
        except Exception:
            return False
    return False

def kill_process_tree(process_info):
    pid = None
    script_key = process_info.get('script_key', 'N/A') 
    try:
        if 'log_file' in process_info and hasattr(process_info['log_file'], 'close') and not process_info['log_file'].closed:
            try: process_info['log_file'].close()
            except Exception: pass

        process = process_info.get('process')
        if process and hasattr(process, 'pid'):
           pid = process.pid
           if pid: 
                try:
                    parent = psutil.Process(pid)
                    children = parent.children(recursive=True)
                    for child in children:
                        try: child.terminate()
                        except psutil.NoSuchProcess: pass
                        except Exception:
                            try: child.kill()
                            except Exception: pass

                    gone, alive = psutil.wait_procs(children, timeout=1)
                    for p in alive:
                        try: p.kill()
                        except Exception: pass

                    try:
                        parent.terminate()
                        try: parent.wait(timeout=1)
                        except psutil.TimeoutExpired: parent.kill()
                    except psutil.NoSuchProcess: pass
                    except Exception:
                        try: parent.kill()
                        except Exception: pass
                except psutil.NoSuchProcess:
                    pass
    except Exception as e:
        logger.error(f"❌ Error killing process tree for PID {pid or 'N/A'} ({script_key}): {e}", exc_info=True)

# --- Automatic Package Installation & Script Running ---
def attempt_install_pip(module_name, message):
    package_name = TELEGRAM_MODULES.get(module_name.lower(), module_name) 
    if package_name is None: 
        return False 
    try:
        bot.reply_to(message, f"🐍 Module `{module_name}` not found. Installing `{package_name}`...", parse_mode='Markdown')
        command = [sys.executable, '-m', 'pip', 'install', package_name]
        result = subprocess.run(command, capture_output=True, text=True, check=False, encoding='utf-8', errors='ignore')
        if result.returncode == 0:
            bot.reply_to(message, f"✅ Package `{package_name}` (for `{module_name}`) installed.", parse_mode='Markdown')
            return True
        else:
            error_msg = f"❌ Failed to install `{package_name}` for `{module_name}`.\nLog:\n```\n{result.stderr or result.stdout}\n```"
            if len(error_msg) > 4000: error_msg = error_msg[:4000] + "\n... (Log truncated)"
            bot.reply_to(message, error_msg, parse_mode='Markdown')
            return False
    except Exception as e:
        bot.reply_to(message, f"❌ Error installing `{package_name}`: {str(e)}")
        return False

def run_script(script_path, script_owner_id, user_folder, file_name, message_obj_for_reply, attempt=1):
    """Run Python script."""
    max_attempts = 2 
    if attempt > max_attempts:
        bot.reply_to(message_obj_for_reply, f"❌ Failed to run '{file_name}' after {max_attempts} attempts. Check logs.")
        return

    script_key = f"{script_owner_id}_{file_name}"

    try:
        if not os.path.exists(script_path):
             bot.reply_to(message_obj_for_reply, f"❌ Error: Script '{file_name}' not found at '{script_path}'!")
             if script_owner_id in user_files:
                 user_files[script_owner_id] = [f for f in user_files.get(script_owner_id, []) if f[0] != file_name]
             remove_user_file_db(script_owner_id, file_name)
             return

        if attempt == 1:
            check_command = [sys.executable, script_path]
            check_proc = None
            try:
                check_proc = subprocess.Popen(check_command, cwd=user_folder, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='ignore')
                stdout, stderr = check_proc.communicate(timeout=5)
                return_code = check_proc.returncode
                if return_code != 0 and stderr:
                    match_py = re.search(r"ModuleNotFoundError: No module named '(.+?)'", stderr)
                    if match_py:
                        module_name = match_py.group(1).strip().strip("'\"")
                        if attempt_install_pip(module_name, message_obj_for_reply):
                            bot.reply_to(message_obj_for_reply, f"🔄 Install successful. Retrying '{file_name}'...")
                            time.sleep(2)
                            threading.Thread(target=run_script, args=(script_path, script_owner_id, user_folder, file_name, message_obj_for_reply, attempt + 1)).start()
                            return
                        else:
                            bot.reply_to(message_obj_for_reply, f"❌ Install failed. Cannot run '{file_name}'.")
                            return
                    else:
                         error_summary = stderr[:500]
                         bot.reply_to(message_obj_for_reply, f"❌ Error in script pre-check for '{file_name}':\n```\n{error_summary}\n```\nFix the script.", parse_mode='Markdown')
                         return
            except subprocess.TimeoutExpired:
                if check_proc and check_proc.poll() is None: check_proc.kill(); check_proc.communicate()
            except FileNotFoundError:
                 bot.reply_to(message_obj_for_reply, f"❌ Error: Python interpreter '{sys.executable}' not found.")
                 return
            except Exception as e:
                 bot.reply_to(message_obj_for_reply, f"❌ Unexpected error in script pre-check for '{file_name}': {e}")
                 return
            finally:
                 if check_proc and check_proc.poll() is None:
                     check_proc.kill(); check_proc.communicate()

        log_dir = os.path.dirname(script_path)
        log_file_path = os.path.join(log_dir, f"{os.path.splitext(os.path.basename(file_name))[0]}.log")
        log_file = None; process = None
        try: log_file = open(log_file_path, 'w', encoding='utf-8', errors='ignore')
        except Exception as e:
             bot.reply_to(message_obj_for_reply, f"❌ Failed to open log file '{log_file_path}': {e}")
             return
        try:
            startupinfo = None; creationflags = 0
            if os.name == 'nt':
                 startupinfo = subprocess.STARTUPINFO(); startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                 startupinfo.wShowWindow = subprocess.SW_HIDE
            process = subprocess.Popen(
                [sys.executable, script_path], cwd=user_folder, stdout=log_file, stderr=log_file,
                stdin=subprocess.PIPE, startupinfo=startupinfo, creationflags=creationflags,
                encoding='utf-8', errors='ignore'
            )
            bot_scripts[script_key] = {
                'process': process, 'log_file': log_file, 'file_name': file_name,
                'chat_id': message_obj_for_reply.chat.id,
                'script_owner_id': script_owner_id,
                'start_time': datetime.now(), 'user_folder': user_folder, 'type': 'py', 'script_key': script_key
            }
            bot.reply_to(message_obj_for_reply, f"✅ Python script `{file_name}` started! (PID: `{process.pid}`)", parse_mode='Markdown')
        except Exception as e:
            if log_file and not log_file.closed: log_file.close()
            bot.reply_to(message_obj_for_reply, f"❌ Error starting Python script '{file_name}': {str(e)}")
            if process and process.poll() is None:
                 kill_process_tree({'process': process, 'log_file': log_file, 'script_key': script_key})
            if script_key in bot_scripts: del bot_scripts[script_key]
    except Exception as e:
        bot.reply_to(message_obj_for_reply, f"❌ Unexpected error running Python script '{file_name}': {str(e)}")
        if script_key in bot_scripts:
             kill_process_tree(bot_scripts[script_key])
             del bot_scripts[script_key]

TELEGRAM_MODULES = {
    'telebot': 'pyTelegramBotAPI',
    'telegram': 'python-telegram-bot',
    'python_telegram_bot': 'python-telegram-bot',
    'aiogram': 'aiogram',
    'pyrogram': 'pyrogram',
    'telethon': 'telethon',
    'telethon.sync': 'telethon',
    'from telethon.sync import telegramclient': 'telethon',
    'telepot': 'telepot',
    'pytg': 'pytg',
    'tgcrypto': 'tgcrypto',
    'telegram_upload': 'telegram-upload',
    'telegram_send': 'telegram-send',
    'telegram_text': 'telegram-text',
    'mtproto': 'telegram-mtproto',
    'tl': 'telethon',
    'bs4': 'beautifulsoup4',
    'requests': 'requests',
    'pillow': 'Pillow',
    'cv2': 'opencv-python',
    'yaml': 'PyYAML',
    'dotenv': 'python-dotenv',
    'dateutil': 'python-dateutil',
    'pandas': 'pandas',
    'numpy': 'numpy',
    'flask': 'Flask',
    'django': 'Django',
    'sqlalchemy': 'SQLAlchemy',
    'psutil': 'psutil',
    'asyncio': None, 'json': None, 'datetime': None, 'os': None, 'sys': None,
    're': None, 'time': None, 'math': None, 'random': None, 'logging': None,
    'threading': None, 'subprocess': None, 'zipfile': None, 'tempfile': None,
    'shutil': None, 'sqlite3': None, 'atexit': None
}

# --- Database Operations ---
DB_LOCK = threading.Lock() 

def save_user_file(user_id, file_name, file_type='py', project_name=None):
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        try:
            display_name = f"{project_name}/{file_name}" if project_name and not file_name.startswith(f"{project_name}/") else file_name
            c.execute('INSERT OR REPLACE INTO user_files (user_id, file_name, file_type) VALUES (?, ?, ?)',
                      (user_id, display_name, file_type))
            conn.commit()
            if user_id not in user_files: user_files[user_id] = []
            user_files[user_id] = [(fn, ft) for fn, ft in user_files[user_id] if fn != display_name]
            user_files[user_id].append((display_name, file_type))
        except Exception as e:
            logger.error(f"❌ Error saving file for {user_id}, {file_name}: {e}", exc_info=True)
        finally:
            conn.close()

def remove_user_file_db(user_id, file_name):
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        try:
            c.execute('DELETE FROM user_files WHERE user_id = ? AND file_name = ?', (user_id, file_name))
            conn.commit()
            if user_id in user_files:
                user_files[user_id] = [f for f in user_files[user_id] if f[0] != file_name]
                if not user_files[user_id]: del user_files[user_id]
        except Exception as e:
            logger.error(f"❌ Error removing file for {user_id}, {file_name}: {e}", exc_info=True)
        finally:
            conn.close()

def add_active_user(user_id):
    active_users.add(user_id) 
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        try:
            c.execute('INSERT OR IGNORE INTO active_users (user_id) VALUES (?)', (user_id,))
            conn.commit()
        except Exception as e:
            logger.error(f"❌ Error adding active user {user_id}: {e}", exc_info=True)
        finally:
            conn.close()

def save_subscription(user_id, expiry):
    """Save subscription where expiry can be a datetime object or 'forever'"""
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        try:
            expiry_str = 'forever' if expiry == 'forever' else expiry.isoformat()
            c.execute('INSERT OR REPLACE INTO subscriptions (user_id, expiry) VALUES (?, ?)', (user_id, expiry_str))
            conn.commit()
            user_subscriptions[user_id] = {'expiry': expiry}
        except Exception as e:
            logger.error(f"❌ Error saving subscription for {user_id}: {e}", exc_info=True)
        finally:
            conn.close()

def remove_subscription_db(user_id):
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        try:
            c.execute('DELETE FROM subscriptions WHERE user_id = ?', (user_id,))
            conn.commit()
            if user_id in user_subscriptions: del user_subscriptions[user_id]
        except Exception as e:
            logger.error(f"❌ Error removing subscription for {user_id}: {e}", exc_info=True)
        finally:
            conn.close()

def add_admin_db(admin_id):
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        try:
            c.execute('INSERT OR IGNORE INTO admins (user_id) VALUES (?)', (admin_id,))
            conn.commit()
            admin_ids.add(admin_id) 
        except Exception as e:
            logger.error(f"❌ Error adding admin {admin_id}: {e}", exc_info=True)
        finally:
            conn.close()

def remove_admin_db(admin_id):
    if admin_id == OWNER_ID:
        return False 
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        removed = False
        try:
            c.execute('SELECT 1 FROM admins WHERE user_id = ?', (admin_id,))
            if c.fetchone():
                c.execute('DELETE FROM admins WHERE user_id = ?', (admin_id,))
                conn.commit()
                removed = c.rowcount > 0 
                if removed: admin_ids.discard(admin_id)
            else:
                admin_ids.discard(admin_id)
            return removed
        except Exception as e:
            logger.error(f"❌ Error removing admin {admin_id}: {e}", exc_info=True)
            return False
        finally:
            conn.close()

def add_force_channel_db(channel_id, invite_link):
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        try:
            c.execute('INSERT OR REPLACE INTO force_channels (channel_id, invite_link) VALUES (?, ?)', (channel_id, invite_link))
            conn.commit()
            force_channels[channel_id] = invite_link
        except Exception as e:
            logger.error(f"❌ Error saving force channel {channel_id}: {e}")
        finally:
            conn.close()

def remove_force_channel_db(channel_id):
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        try:
            c.execute('DELETE FROM force_channels WHERE channel_id = ?', (channel_id,))
            conn.commit()
            return force_channels.pop(channel_id, None) is not None
        except Exception as e:
            logger.error(f"❌ Error removing force channel {channel_id}: {e}")
            return False
        finally:
            conn.close()
# --- End Database Operations ---

# --- Menu creation (Upload File on Top) ---
def create_main_menu_inline(user_id):
    markup = types.InlineKeyboardMarkup(row_width=2)
    # Primary Update Channel Link (uses first admin force channel if set, else UPDATE_CHANNEL)
    primary_channel_url = next(iter(force_channels.values()), UPDATE_CHANNEL)

    upload_btn = types.InlineKeyboardButton('📤 Upload File', callback_data='upload')
    check_btn = types.InlineKeyboardButton('📂 Check Files', callback_data='check_files')
    speed_btn = types.InlineKeyboardButton('⚡ Bot Speed', callback_data='speed')
    channel_btn = types.InlineKeyboardButton('📢 Updates Channel', url=primary_channel_url)
    contact_btn = types.InlineKeyboardButton('📞 Contact Owner', url=f'https://t.me/{YOUR_USERNAME.replace("@", "")}')

    # TOP ROW: Upload File & Check Files
    markup.add(upload_btn, check_btn)

    if user_id in admin_ids:
        admin_buttons = [
            types.InlineKeyboardButton('💳 Subscriptions', callback_data='subscription'),
            types.InlineKeyboardButton('📊 Statistics', callback_data='stats'),
            types.InlineKeyboardButton('🔒 Lock Bot' if not bot_locked else '🔓 Unlock Bot',
                                     callback_data='lock_bot' if not bot_locked else 'unlock_bot'),
            types.InlineKeyboardButton('📢 Broadcast', callback_data='broadcast'),
            types.InlineKeyboardButton('👑 Admin Panel', callback_data='admin_panel'),
            types.InlineKeyboardButton('🟢 Run All User Scripts', callback_data='run_all_scripts')
        ]
        markup.add(speed_btn, admin_buttons[0])
        markup.add(admin_buttons[1], admin_buttons[3])
        markup.add(admin_buttons[2], admin_buttons[5])
        if user_id == OWNER_ID:
            markup.add(types.InlineKeyboardButton('🗑️ Remove User Files (Owner)', callback_data='owner_remove_menu'))
        markup.add(admin_buttons[4])
        markup.add(channel_btn, contact_btn)
    else:
        markup.add(speed_btn, types.InlineKeyboardButton('📊 Statistics', callback_data='stats'))
        markup.add(channel_btn, contact_btn)
    return markup

def create_reply_keyboard_main_menu(user_id):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    layout_to_use = ADMIN_COMMAND_BUTTONS_LAYOUT_USER_SPEC if user_id in admin_ids else COMMAND_BUTTONS_LAYOUT_USER_SPEC
    for row_buttons_text in layout_to_use:
        markup.add(*[types.KeyboardButton(text) for text in row_buttons_text])
    return markup

def create_upload_category_markup():
    """Step 1 of Upload: Only Python button (HTML removed)"""
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(types.InlineKeyboardButton("🐍 Python", callback_data="up_type_python"))
    markup.add(types.InlineKeyboardButton("🔙 Back to Main", callback_data="back_to_main"))
    return markup

def create_upload_mode_markup():
    """Step 2 of Upload: Choose Files or Choose Folder"""
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("📄 Choose Files", callback_data="up_mode_python_file"),
        types.InlineKeyboardButton("📁 Choose Folder", callback_data="up_mode_python_folder")
    )
    markup.add(types.InlineKeyboardButton("🔙 Back", callback_data="upload"))
    return markup

def create_control_buttons(script_owner_id, file_name, is_running=True):
    markup = types.InlineKeyboardMarkup(row_width=2)
    if is_running:
        markup.row(
            types.InlineKeyboardButton("🔴 Stop", callback_data=f'stop_{script_owner_id}_{file_name}'),
            types.InlineKeyboardButton("🔄 Restart", callback_data=f'restart_{script_owner_id}_{file_name}')
        )
        markup.row(
            types.InlineKeyboardButton("🗑️ Delete", callback_data=f'delete_{script_owner_id}_{file_name}'),
            types.InlineKeyboardButton("📜 Logs", callback_data=f'logs_{script_owner_id}_{file_name}')
        )
    else:
        markup.row(
            types.InlineKeyboardButton("🟢 Start", callback_data=f'start_{script_owner_id}_{file_name}'),
            types.InlineKeyboardButton("🗑️ Delete", callback_data=f'delete_{script_owner_id}_{file_name}')
        )
        markup.row(
            types.InlineKeyboardButton("📜 View Logs", callback_data=f'logs_{script_owner_id}_{file_name}')
        )
    markup.add(types.InlineKeyboardButton("🔙 Back to Files", callback_data='check_files'))
    return markup

def create_admin_panel():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.row(
        types.InlineKeyboardButton('➕ Add Admin', callback_data='add_admin'),
        types.InlineKeyboardButton('➖ Remove Admin', callback_data='remove_admin')
    )
    markup.row(types.InlineKeyboardButton('📋 List Admins', callback_data='list_admins'))
    markup.row(types.InlineKeyboardButton('🔗 Force Join Channels (Unlock Links)', callback_data='force_join_menu'))
    markup.row(types.InlineKeyboardButton('🔙 Back to Main', callback_data='back_to_main'))
    return markup

def create_force_join_panel():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.row(
        types.InlineKeyboardButton('➕ Add Channel Link', callback_data='add_force_ch'),
        types.InlineKeyboardButton('➖ Remove Channel', callback_data='remove_force_ch')
    )
    markup.row(types.InlineKeyboardButton('📋 List Active Channels', callback_data='list_force_ch'))
    markup.row(types.InlineKeyboardButton('🔙 Back to Admin Panel', callback_data='admin_panel'))
    return markup

def create_subscription_menu():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.row(
        types.InlineKeyboardButton('➕ Add Sub (Days)', callback_data='add_subscription'),
        types.InlineKeyboardButton('♾️ Add Forever Sub', callback_data='add_forever_subscription')
    )
    markup.row(
        types.InlineKeyboardButton('➖ Remove Sub', callback_data='remove_subscription'),
        types.InlineKeyboardButton('🔍 Check Sub', callback_data='check_subscription')
    )
    markup.row(types.InlineKeyboardButton('🔙 Back to Main', callback_data='back_to_main'))
    return markup
# --- End Menu Creation ---

# --- File Handling (ZIP & Python) ---
def handle_zip_file(downloaded_file_content, file_name_zip, message):
    user_id = message.from_user.id
    user_folder = get_user_folder(user_id)
    temp_dir = None 
    
    project_name = os.path.splitext(file_name_zip)[0]
    project_folder = os.path.join(user_folder, project_name)
    os.makedirs(project_folder, exist_ok=True)
    
    try:
        temp_dir = tempfile.mkdtemp(prefix=f"user_{user_id}_zip_")
        zip_path = os.path.join(temp_dir, file_name_zip)
        with open(zip_path, 'wb') as new_file: new_file.write(downloaded_file_content)
        
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            for member in zip_ref.infolist():
                member_path = os.path.abspath(os.path.join(temp_dir, member.filename))
                if not member_path.startswith(os.path.abspath(temp_dir)):
                    raise zipfile.BadZipFile(f"Zip has unsafe path: {member.filename}")
            zip_ref.extractall(temp_dir)

        os.remove(zip_path)

        extracted_items = os.listdir(temp_dir)
        py_files = [f for f in extracted_items if f.endswith('.py')]
        req_file = 'requirements.txt' if 'requirements.txt' in extracted_items else None

        if req_file:
            req_path = os.path.join(temp_dir, req_file)
            bot.reply_to(message, f"🔄 Installing Python dependencies from `{req_file}`...", parse_mode='Markdown')
            try:
                command = [sys.executable, '-m', 'pip', 'install', '-r', req_path]
                subprocess.run(command, capture_output=True, text=True, check=True, encoding='utf-8', errors='ignore')
                bot.reply_to(message, f"✅ Dependencies from `{req_file}` installed.", parse_mode='Markdown')
            except subprocess.CalledProcessError as e:
                error_msg = f"❌ Failed to install dependencies from `{req_file}`.\nLog:\n```\n{e.stderr or e.stdout}\n```"
                if len(error_msg) > 4000: error_msg = error_msg[:4000] + "\n... (Log truncated)"
                bot.reply_to(message, error_msg, parse_mode='Markdown'); return
            except Exception as e:
                 bot.reply_to(message, f"❌ Unexpected error installing dependencies: {e}"); return

        main_script_name = None
        preferred_py = ['main.py', 'bot.py', 'app.py']
        for p in preferred_py:
            if p in py_files: main_script_name = p; break
        if not main_script_name and py_files:
            main_script_name = py_files[0]
        if not main_script_name:
            bot.reply_to(message, "❌ No `.py` script found inside the uploaded Folder (ZIP)!"); return

        for item_name in os.listdir(temp_dir):
            src_path = os.path.join(temp_dir, item_name)
            dest_path = os.path.join(project_folder, item_name)
            if os.path.isdir(dest_path): shutil.rmtree(dest_path)
            elif os.path.exists(dest_path): os.remove(dest_path)
            shutil.move(src_path, dest_path)

        display_file_key = f"{project_name}/{main_script_name}"
        save_user_file(user_id, display_file_key, 'py')
        
        main_script_path = os.path.join(project_folder, main_script_name)
        bot.reply_to(message, f"✅ Folder extracted to `{project_name}/`. Starting main script: `{main_script_name}`...", parse_mode='Markdown')

        threading.Thread(target=run_script, args=(main_script_path, user_id, project_folder, display_file_key, message)).start()

    except zipfile.BadZipFile as e:
        bot.reply_to(message, f"❌ Error: Invalid/corrupted ZIP. {e}")
    except Exception as e:
        logger.error(f"❌ Error processing zip for {user_id}: {e}", exc_info=True)
        bot.reply_to(message, f"❌ Error processing zip: {str(e)}")
    finally:
        if temp_dir and os.path.exists(temp_dir):
            try: shutil.rmtree(temp_dir)
            except Exception: pass

def handle_py_file(file_path, script_owner_id, user_folder, file_name, message):
    try:
        save_user_file(script_owner_id, file_name, 'py')
        threading.Thread(target=run_script, args=(file_path, script_owner_id, user_folder, file_name, message)).start()
    except Exception as e:
        bot.reply_to(message, f"❌ Error processing Python file: {str(e)}")
# --- End File Handling ---

# --- Welcome & Logic Functions ---
def get_user_status_and_expiry(user_id):
    expiry_info = ""
    if user_id == OWNER_ID:
        user_status = "👑 Owner (Supreme)"
    elif user_id in admin_ids:
        user_status = "🛡️ Administrator"
    elif user_id in user_subscriptions:
        expiry = user_subscriptions[user_id].get('expiry')
        if expiry == 'forever':
            user_status = "💎 Premium (♾️ Forever)"
            expiry_info = "\n⏳ **Validity:** `♾️ Lifetime Access`"
        elif isinstance(expiry, datetime) and expiry > datetime.now():
            days_left = (expiry - datetime.now()).days
            user_status = "⭐ Premium Member"
            expiry_info = f"\n⏳ **Expires in:** `{days_left} Days`"
        else:
            user_status = "🆓 Free User (Expired)"
            remove_subscription_db(user_id)
    else:
        user_status = "🆓 Free User"
    return user_status, expiry_info

def _logic_send_welcome(message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    user_name = message.from_user.first_name
    user_username = message.from_user.username

    if user_id not in active_users:
        add_active_user(user_id)

    if bot_locked and user_id not in admin_ids:
        bot.send_message(chat_id, "⚠️ **Bot is currently locked by Admin.** Try again later.", parse_mode='Markdown')
        return

    # Check Force Join / Join to Unlock
    joined, _ = is_user_joined_all(user_id)
    if not joined:
        send_force_join_prompt(chat_id, user_id)
        return

    photo_file_id = None
    try:
        user_profile_photos = bot.get_user_profile_photos(user_id, limit=1)
        if user_profile_photos and user_profile_photos.photos:
            photo_file_id = user_profile_photos.photos[0][-1].file_id
    except Exception:
        pass

    file_limit = get_user_file_limit(user_id)
    current_files = get_user_file_count(user_id)
    limit_str = str(file_limit) if file_limit != float('inf') else "♾️ Unlimited"
    user_status, expiry_info = get_user_status_and_expiry(user_id)

    welcome_msg_text = (
        f"⚡✨ **𝐖𝐄𝐋𝐂𝐎𝐌𝐄 𝐓𝐎 𝐁𝐔𝐗𝐗𝐌𝐎𝐃𝐙 𝐇𝐎𝐒𝐓** ✨⚡\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 **User:** {user_name}\n"
        f"🆔 **User ID:** `{user_id}`\n"
        f"🌐 **Username:** `@{user_username or 'None'}`\n"
        f"🔰 **Plan Status:** {user_status}{expiry_info}\n"
        f"📦 **Storage Used:** `{current_files} / {limit_str}`\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🚀 **𝐏𝐫𝐞𝐦𝐢𝐮𝐦 𝐏𝐲𝐭𝐡𝐨𝐧 𝐂𝐥𝐨𝐮𝐝 𝐇𝐨𝐬𝐭𝐢𝐧𝐠:**\n"
        f"┣ 🐍 **Single File:** Upload `.py` scripts\n"
        f"┣ 📁 **Project Folder:** Upload `.zip` with `requirements.txt`\n"
        f"┗ ⚡ **Auto-Install:** Automatic pip module installer\n\n"
        f"💎 _Tap **📤 Upload File** at the top of your menu to deploy!_"
    )
    
    main_reply_markup = create_reply_keyboard_main_menu(user_id)
    inline_markup = create_main_menu_inline(user_id)
    
    try:
        if photo_file_id:
            bot.send_photo(chat_id, photo_file_id, caption=welcome_msg_text, reply_markup=inline_markup, parse_mode='Markdown')
            bot.send_message(chat_id, "✨ **Quick Menu Keyboard Activated** 👇", reply_markup=main_reply_markup, parse_mode='Markdown')
        else:
            bot.send_message(chat_id, welcome_msg_text, reply_markup=inline_markup, parse_mode='Markdown')
            bot.send_message(chat_id, "✨ **Quick Menu Keyboard Activated** 👇", reply_markup=main_reply_markup, parse_mode='Markdown')
    except Exception:
        bot.send_message(chat_id, welcome_msg_text, reply_markup=main_reply_markup, parse_mode='Markdown')

def _logic_updates_channel(message):
    user_id = message.from_user.id
    joined, _ = is_user_joined_all(user_id)
    if not joined:
        send_force_join_prompt(message.chat.id, user_id)
        return
    markup = types.InlineKeyboardMarkup()
    if force_channels:
        idx = 1
        for _, link in force_channels.items():
            markup.add(types.InlineKeyboardButton(f'📢 Official Channel #{idx}', url=link))
            idx += 1
    else:
        markup.add(types.InlineKeyboardButton('📢 Updates Channel', url=UPDATE_CHANNEL))
    bot.reply_to(message, "📢 **Join our Official Updates Channel(s):**", reply_markup=markup, parse_mode='Markdown')

def _logic_upload_file(message):
    user_id = message.from_user.id
    if bot_locked and user_id not in admin_ids:
        bot.reply_to(message, "⚠️ Bot locked by admin, cannot accept files.")
        return

    joined, _ = is_user_joined_all(user_id)
    if not joined:
        send_force_join_prompt(message.chat.id, user_id)
        return

    file_limit = get_user_file_limit(user_id)
    current_files = get_user_file_count(user_id)
    if current_files >= file_limit:
        limit_str = str(file_limit) if file_limit != float('inf') else "Unlimited"
        bot.reply_to(message, f"⚠️ File limit ({current_files}/{limit_str}) reached. Delete files first.")
        return

    bot.reply_to(
        message,
        "📤 **Select Hosting Language:**\n\nClick the button below to proceed with Python hosting:",
        reply_markup=create_upload_category_markup(),
        parse_mode='Markdown'
    )

def _logic_check_files(message):
    user_id = message.from_user.id
    joined, _ = is_user_joined_all(user_id)
    if not joined:
        send_force_join_prompt(message.chat.id, user_id)
        return

    user_files_list = user_files.get(user_id, [])
    if not user_files_list:
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("📤 Upload New File", callback_data="upload"))
        bot.reply_to(
            message,
            "📂 **Your Hosted Python Files:**\n\n_(You haven't uploaded any Python scripts or folders yet)_",
            reply_markup=markup,
            parse_mode='Markdown'
        )
        return

    markup = types.InlineKeyboardMarkup(row_width=1)
    for file_name, file_type in sorted(user_files_list):
        is_running = is_bot_running(user_id, file_name)
        status_icon = "🟢 Running" if is_running else "🔴 Stopped"
        btn_text = f"🐍 {file_name} ({file_type.upper()}) - {status_icon}"
        markup.add(types.InlineKeyboardButton(btn_text, callback_data=f'file_{user_id}_{file_name}'))

    markup.add(types.InlineKeyboardButton("📤 Upload More", callback_data="upload"))
    bot.reply_to(
        message,
        f"📂 **Your Hosted Python Scripts ({len(user_files_list)}):**\n\n"
        f"👇 Click on any script or project folder below to Start, Stop, Restart, Delete, or View Logs:",
        reply_markup=markup,
        parse_mode='Markdown'
    )

def _logic_bot_speed(message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    start_time_ping = time.time()
    wait_msg = bot.reply_to(message, "🏃 Testing speed...")
    try:
        bot.send_chat_action(chat_id, 'typing')
        response_time = round((time.time() - start_time_ping) * 1000, 2)
        status = "🔓 Unlocked" if not bot_locked else "🔒 Locked"
        user_level, _ = get_user_status_and_expiry(user_id)
        speed_msg = (f"⚡ **Bot Speed & Status:**\n\n"
                     f"⏱️ **API Response Time:** `{response_time} ms`\n"
                     f"🚦 **Bot Status:** {status}\n"
                     f"👤 **Your Level:** {user_level}")
        bot.edit_message_text(speed_msg, chat_id, wait_msg.message_id, parse_mode='Markdown')
    except Exception:
        bot.edit_message_text("❌ Error during speed test.", chat_id, wait_msg.message_id)

def _logic_contact_owner(message):
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton('📞 Contact Owner', url=f'https://t.me/{YOUR_USERNAME.replace("@", "")}'))
    bot.reply_to(message, "👑 **Click below to contact the Owner:**", reply_markup=markup, parse_mode='Markdown')

# --- Admin Logic Functions ---
def _logic_subscriptions_panel(message):
    if message.from_user.id not in admin_ids:
        bot.reply_to(message, "⚠️ Admin permissions required.")
        return
    bot.reply_to(message, "💳 **Subscription Management**\nChoose an option below:", reply_markup=create_subscription_menu(), parse_mode='Markdown')

def _logic_statistics(message):
    user_id = message.from_user.id
    total_users = len(active_users)
    total_files_records = sum(len(files) for files in user_files.values())

    running_bots_count = 0
    user_running_bots = 0

    for script_key_iter, script_info_iter in list(bot_scripts.items()):
        s_owner_id, _ = script_key_iter.split('_', 1)
        if is_bot_running(int(s_owner_id), script_info_iter['file_name']):
            running_bots_count += 1
            if int(s_owner_id) == user_id:
                user_running_bots += 1

    stats_msg_base = (f"📊 **Bot Live Statistics:**\n\n"
                      f"👥 **Total Users:** `{total_users}`\n"
                      f"📂 **Total Scripts Hosted:** `{total_files_records}`\n"
                      f"🟢 **Total Active Bots:** `{running_bots_count}`\n")

    if user_id in admin_ids:
        stats_msg_admin = (f"🔒 **Bot Status:** `{'🔴 Locked' if bot_locked else '🟢 Unlocked'}`\n"
                           f"🔗 **Force Join Channels:** `{len(force_channels)}`\n"
                           f"🤖 **Your Running Bots:** `{user_running_bots}`")
        stats_msg = stats_msg_base + stats_msg_admin
    else:
        stats_msg = stats_msg_base + f"🤖 **Your Running Bots:** `{user_running_bots}`"

    bot.reply_to(message, stats_msg, parse_mode='Markdown')

def _logic_broadcast_init(message):
    if message.from_user.id not in admin_ids:
        bot.reply_to(message, "⚠️ Admin permissions required.")
        return
    msg = bot.reply_to(message, "📢 Send message to broadcast to all active users.\n/cancel to abort.")
    bot.register_next_step_handler(msg, process_broadcast_message)

def _logic_toggle_lock_bot(message):
    if message.from_user.id not in admin_ids:
        bot.reply_to(message, "⚠️ Admin permissions required.")
        return
    global bot_locked
    bot_locked = not bot_locked
    status = "locked" if bot_locked else "unlocked"
    bot.reply_to(message, f"🔒 Bot has been **{status}**.", parse_mode='Markdown')

def _logic_admin_panel(message):
    if message.from_user.id not in admin_ids:
        bot.reply_to(message, "⚠️ Admin permissions required.")
        return
    bot.reply_to(message, "👑 **Admin Control Panel**\nManage admins and Force-Join unlock links:", reply_markup=create_admin_panel(), parse_mode='Markdown')

def _logic_run_all_scripts(message_or_call):
    if isinstance(message_or_call, telebot.types.Message):
        admin_user_id = message_or_call.from_user.id
        admin_chat_id = message_or_call.chat.id
        reply_func = lambda text, **kwargs: bot.reply_to(message_or_call, text, **kwargs)
        admin_message_obj_for_script_runner = message_or_call
    elif isinstance(message_or_call, telebot.types.CallbackQuery):
        admin_user_id = message_or_call.from_user.id
        admin_chat_id = message_or_call.message.chat.id
        bot.answer_callback_query(message_or_call.id)
        reply_func = lambda text, **kwargs: bot.send_message(admin_chat_id, text, **kwargs)
        admin_message_obj_for_script_runner = message_or_call.message 
    else:
        return

    if admin_user_id not in admin_ids:
        reply_func("⚠️ Admin permissions required.")
        return

    reply_func("⏳ Starting process to run all user Python scripts...")

    started_count = 0; attempted_users = 0; skipped_files = 0
    all_user_files_snapshot = dict(user_files)

    for target_user_id, files_for_user in all_user_files_snapshot.items():
        if not files_for_user: continue
        attempted_users += 1
        user_folder = get_user_folder(target_user_id)

        for file_name, file_type in files_for_user:
            if not is_bot_running(target_user_id, file_name):
                file_path = os.path.join(user_folder, file_name)
                run_dir = os.path.dirname(file_path)
                if os.path.exists(file_path):
                    try:
                        threading.Thread(target=run_script, args=(file_path, target_user_id, run_dir, file_name, admin_message_obj_for_script_runner)).start()
                        started_count += 1
                        time.sleep(0.7)
                    except Exception:
                        skipped_files += 1
                else:
                    skipped_files += 1

    summary_msg = (f"✅ **All Users' Scripts — Processing Complete:**\n\n"
                   f"▶️ **Attempted to start:** `{started_count}` scripts.\n"
                   f"👥 **Users processed:** `{attempted_users}`.\n")
    if skipped_files > 0:
        summary_msg += f"⚠️ **Skipped/Error files:** `{skipped_files}`\n"

    reply_func(summary_msg, parse_mode='Markdown')

# --- Command & Text Handlers ---
@bot.message_handler(commands=['start', 'help'])
def command_send_welcome(message): _logic_send_welcome(message)

@bot.message_handler(commands=['status'])
def command_show_status(message): _logic_statistics(message)

BUTTON_TEXT_TO_LOGIC = {
    "📤 Upload File": _logic_upload_file,
    "📂 Check Files": _logic_check_files,
    "⚡ Bot Speed": _logic_bot_speed,
    "📊 Statistics": _logic_statistics,
    "📢 Updates Channel": _logic_updates_channel,
    "📞 Contact Owner": _logic_contact_owner,
    "💳 Subscriptions": _logic_subscriptions_panel,
    "📢 Broadcast": _logic_broadcast_init,
    "🔒 Lock Bot": _logic_toggle_lock_bot, 
    "🟢 Running All Code": _logic_run_all_scripts,
    "👑 Admin Panel": _logic_admin_panel,
}

@bot.message_handler(func=lambda message: message.text in BUTTON_TEXT_TO_LOGIC)
def handle_button_text(message):
    logic_func = BUTTON_TEXT_TO_LOGIC.get(message.text)
    if logic_func: logic_func(message)

@bot.message_handler(commands=['updateschannel'])
def command_updates_channel(message): _logic_updates_channel(message)
@bot.message_handler(commands=['uploadfile', 'upload'])
def command_upload_file(message): _logic_upload_file(message)
@bot.message_handler(commands=['checkfiles', 'files'])
def command_check_files(message): _logic_check_files(message)
@bot.message_handler(commands=['botspeed', 'speed'])
def command_bot_speed(message): _logic_bot_speed(message)
@bot.message_handler(commands=['contactowner'])
def command_contact_owner(message): _logic_contact_owner(message)
@bot.message_handler(commands=['subscriptions'])
def command_subscriptions(message): _logic_subscriptions_panel(message)
@bot.message_handler(commands=['statistics'])
def command_statistics(message): _logic_statistics(message)
@bot.message_handler(commands=['broadcast'])
def command_broadcast(message): _logic_broadcast_init(message)
@bot.message_handler(commands=['lockbot']) 
def command_lock_bot(message): _logic_toggle_lock_bot(message)
@bot.message_handler(commands=['adminpanel'])
def command_admin_panel(message): _logic_admin_panel(message)
@bot.message_handler(commands=['runningallcode'])
def command_run_all_code(message): _logic_run_all_scripts(message)

@bot.message_handler(commands=['ping'])
def ping(message):
    start_ping_time = time.time() 
    msg = bot.reply_to(message, "Pong!")
    latency = round((time.time() - start_ping_time) * 1000, 2)
    bot.edit_message_text(f"Pong! Latency: {latency} ms", message.chat.id, msg.message_id)

# --- Document (File) Handler (Python & ZIP Only) ---
@bot.message_handler(content_types=['document'])
def handle_file_upload_doc(message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    doc = message.document

    if bot_locked and user_id not in admin_ids:
        bot.reply_to(message, "⚠️ Bot locked, cannot accept files.")
        return

    joined, _ = is_user_joined_all(user_id)
    if not joined:
        send_force_join_prompt(chat_id, user_id)
        return

    file_limit = get_user_file_limit(user_id)
    current_files = get_user_file_count(user_id)
    if current_files >= file_limit:
        limit_str = str(file_limit) if file_limit != float('inf') else "Unlimited"
        bot.reply_to(message, f"⚠️ File limit ({current_files}/{limit_str}) reached. Delete files via /checkfiles.")
        return

    file_name = doc.file_name
    if not file_name: 
        bot.reply_to(message, "⚠️ No file name found.")
        return
        
    file_ext = os.path.splitext(file_name)[1].lower()
    if file_ext not in ['.py', '.zip']:
        bot.reply_to(message, "⚠️ Unsupported file type! Only Python (`.py`) files or Python Project Folders (`.zip`) are allowed.")
        return
        
    max_file_size = 20 * 1024 * 1024 # 20 MB
    if doc.file_size > max_file_size:
        bot.reply_to(message, f"⚠️ File too large (Max: {max_file_size // 1024 // 1024} MB).")
        return

    state = user_upload_state.get(user_id)
    if state:
        mode = state.get('mode')
        if mode == 'folder' and file_ext != '.zip':
            bot.reply_to(message, "⚠️ You selected **Choose Folder**. Please compress your folder as a `.zip` archive and send it, or switch to **Choose Files**.", parse_mode='Markdown')
            return
        if mode == 'file' and file_ext != '.py':
            bot.reply_to(message, "⚠️ You selected **Choose Files**. Please send a `.py` file!", parse_mode='Markdown')
            return

    try:
        download_wait_msg = bot.reply_to(message, f"⏳ Downloading `{file_name}`...", parse_mode='Markdown')
        file_info_tg_doc = bot.get_file(doc.file_id)
        downloaded_file_content = bot.download_file(file_info_tg_doc.file_path)
        bot.edit_message_text(f"✅ Downloaded `{file_name}`. Deploying immediately...", chat_id, download_wait_msg.message_id, parse_mode='Markdown')
        
        user_folder = get_user_folder(user_id)
        user_upload_state.pop(user_id, None)

        if file_ext == '.zip':
            handle_zip_file(downloaded_file_content, file_name, message)
        else:
            file_path = os.path.join(user_folder, file_name)
            with open(file_path, 'wb') as f: 
                f.write(downloaded_file_content)
            handle_py_file(file_path, user_id, user_folder, file_name, message)

    except telebot.apihelper.ApiTelegramException as e:
         bot.reply_to(message, f"❌ Telegram API Error: {str(e)}")
    except Exception as e:
        logger.error(f"❌ General error handling file for {user_id}: {e}", exc_info=True)
        bot.reply_to(message, f"❌ Unexpected error: {str(e)}")
# --- End Document Handler ---

def show_user_files_for_owner(message, target_user_id):
    user_files_list = user_files.get(target_user_id, [])
    if not user_files_list:
        bot.reply_to(message, f"📂 User `{target_user_id}` has no files uploaded.", parse_mode='Markdown')
        owner_remove_mode[OWNER_ID] = False
        return
    
    user_info = "Unknown User"
    try:
        chat = bot.get_chat(target_user_id)
        username = f"@{chat.username}" if chat.username else "No username"
        user_info = f"{chat.first_name} {chat.last_name or ''} ({username})".strip()
    except:
        user_info = f"User ID: {target_user_id}"
    
    markup = types.InlineKeyboardMarkup(row_width=1)
    file_mapping = {}
    
    for full_path, file_type in sorted(user_files_list):
        if '/' in full_path:
            project_name, filename = full_path.split('/', 1)
            display_name = filename
            file_mapping[display_name] = {'project': project_name, 'full': full_path}
        else:
            display_name = full_path
            file_mapping[display_name] = {'project': None, 'full': full_path}
        
        is_running = is_bot_running(target_user_id, full_path)
        status_icon = "🟢 Running" if is_running else "🔴 Stopped"
        
        btn_text = f"🐍 {display_name} ({file_type}) - {status_icon}"
        markup.add(types.InlineKeyboardButton(btn_text, callback_data=f"owner_del_{target_user_id}_{display_name}"))
    
    if not hasattr(bot, 'file_mapping'):
        bot.file_mapping = {}
    bot.file_mapping[f"{target_user_id}"] = file_mapping
    
    markup.add(types.InlineKeyboardButton("🔙 Cancel", callback_data="owner_cancel_remove"))
    
    bot.reply_to(message, f"👤 **Target User:** {user_info}\n🆔 **User ID:** `{target_user_id}`\n📁 **Total Files:** {len(user_files_list)}\n\nClick on any file to delete it:", reply_markup=markup, parse_mode='Markdown')
    owner_remove_mode[OWNER_ID] = False

@bot.message_handler(func=lambda message: owner_remove_mode.get(OWNER_ID, False) and message.from_user.id == OWNER_ID)
def owner_process_user_input(message):
    if message.text.lower() == '/cancel':
        owner_remove_mode[OWNER_ID] = False
        bot.reply_to(message, "❌ Remove mode cancelled.")
        return
    
    target_input = message.text.strip()
    target_user_id = None
    
    if target_input.startswith('@'):
        found_users = []
        for uid in list(active_users):
            try:
                chat = bot.get_chat(uid)
                if chat.username and chat.username.lower() == target_input.lower().replace('@', ''):
                    found_users.append((uid, chat.username, chat.first_name))
            except:
                continue
        
        if len(found_users) == 1:
            target_user_id = found_users[0][0]
        elif len(found_users) > 1:
            markup = types.InlineKeyboardMarkup(row_width=1)
            for uid, uname, fname in found_users:
                markup.add(types.InlineKeyboardButton(f"{fname} (@{uname}) - ID: {uid}"[:50], callback_data=f"owner_select_{uid}"))
            bot.reply_to(message, "Multiple users found. Select one:", reply_markup=markup)
            return
        else:
            bot.reply_to(message, "❌ No user found with this username.")
            return
    else:
        try:
            target_user_id = int(target_input)
        except ValueError:
            bot.reply_to(message, "❌ Invalid input. Send numeric User ID or username with @.")
            return
    
    show_user_files_for_owner(message, target_user_id)

@bot.callback_query_handler(func=lambda call: call.data.startswith('owner_del_'))
def owner_delete_file_callback(call):
    if call.from_user.id != OWNER_ID:
        bot.answer_callback_query(call.id, "⚠️ Owner only.", show_alert=True)
        return
    
    parts = call.data.split('_')
    target_user_id = int(parts[2])
    display_name = '_'.join(parts[3:])
    bot.answer_callback_query(call.id)
    
    file_mapping = getattr(bot, 'file_mapping', {}).get(f"{target_user_id}", {})
    file_info = file_mapping.get(display_name, {})
    project_name = file_info.get('project')
    
    if project_name:
        confirm_text = f"⚠️ **CONFIRM DELETION**\n\nUser ID: `{target_user_id}`\nFile: `{display_name}`\nProject: `{project_name}/`\n\nThis will delete the ENTIRE project folder!"
    else:
        confirm_text = f"⚠️ **CONFIRM DELETION**\n\nUser ID: `{target_user_id}`\nFile: `{display_name}`"
    
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("✅ YES, Delete", callback_data=f"owner_confirm_{target_user_id}_{display_name}"),
        types.InlineKeyboardButton("❌ NO, Cancel", callback_data="owner_cancel_remove")
    )
    
    bot.edit_message_text(confirm_text, chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup, parse_mode='Markdown')

@bot.callback_query_handler(func=lambda call: call.data.startswith('owner_confirm_'))
def owner_confirm_delete_callback(call):
    if call.from_user.id != OWNER_ID:
        bot.answer_callback_query(call.id, "⚠️ Owner only.", show_alert=True)
        return
    
    parts = call.data.split('_')
    target_user_id = int(parts[2])
    display_name = '_'.join(parts[3:])
    bot.answer_callback_query(call.id, "🗑️ Deleting...")
    
    file_mapping = getattr(bot, 'file_mapping', {}).get(f"{target_user_id}", {})
    file_info = file_mapping.get(display_name, {})
    file_path = file_info.get('full', display_name)

    script_key = f"{target_user_id}_{file_path}"
    if is_bot_running(target_user_id, file_path):
        process_info = bot_scripts.get(script_key)
        if process_info:
            kill_process_tree(process_info)
            if script_key in bot_scripts: del bot_scripts[script_key]
    
    user_folder = get_user_folder(target_user_id)
    if '/' in file_path:
        project_name = file_path.split('/')[0]
        project_folder = os.path.join(user_folder, project_name)
    else:
        project_folder = user_folder
    
    if os.path.exists(project_folder) and project_folder != user_folder:
        try:
            shutil.rmtree(project_folder)
        except Exception as e:
            logger.error(f"Error deleting project folder: {e}")
    else:
        file_name = os.path.basename(file_path)
        file_full_path = os.path.join(user_folder, file_name)
        if os.path.exists(file_full_path):
            os.remove(file_full_path)
    
    if '/' in file_path:
        project_name = file_path.split('/')[0]
        if target_user_id in user_files:
            user_files[target_user_id] = [(f, t) for f, t in user_files[target_user_id] if not f.startswith(f"{project_name}/")]
            if not user_files[target_user_id]: del user_files[target_user_id]
        with DB_LOCK:
            conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
            c = conn.cursor()
            c.execute('DELETE FROM user_files WHERE user_id = ? AND file_name LIKE ?', (target_user_id, f"{project_name}/%"))
            conn.commit()
            conn.close()
    else:
        remove_user_file_db(target_user_id, file_path)
    
    bot.edit_message_text("✅ **Deletion Complete**\n\nDeleted files/folders successfully.", chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode='Markdown')

@bot.callback_query_handler(func=lambda call: call.data == 'owner_cancel_remove')
def owner_cancel_remove_callback(call):
    if call.from_user.id != OWNER_ID:
        bot.answer_callback_query(call.id, "⚠️ Owner only.", show_alert=True)
        return
    bot.answer_callback_query(call.id, "❌ Cancelled.")
    bot.edit_message_text("❌ File deletion cancelled.", chat_id=call.message.chat.id, message_id=call.message.message_id)

def handle_callbacks(call):
    user_id = call.from_user.id
    data = call.data

    # Handle Join-to-Unlock Verification Callback
    if data == 'verify_join_unlock':
        joined, _ = is_user_joined_all(user_id)
        if joined:
            bot.answer_callback_query(call.id, "✅ Verification Successful! Bot Unlocked.", show_alert=True)
            back_to_main_callback(call)
        else:
            bot.answer_callback_query(call.id, "❌ You haven't joined all required channels yet! Please join and try again.", show_alert=True)
        return

    # Enforce Force Join on all user actions
    joined, _ = is_user_joined_all(user_id)
    if not joined:
        bot.answer_callback_query(call.id, "🔒 Please join our channel(s) first to unlock the bot!", show_alert=True)
        send_force_join_prompt(call.message.chat.id, user_id, call.message.message_id)
        return

    if bot_locked and user_id not in admin_ids and data not in ['back_to_main', 'speed', 'stats']:
        bot.answer_callback_query(call.id, "⚠️ Bot locked by admin.", show_alert=True)
        return
    try:
        if data == 'upload': upload_callback(call)
        elif data == 'up_type_python': upload_type_callback(call)
        elif data.startswith('up_mode_python_'): upload_mode_callback(call)
        elif data == 'check_files': check_files_callback(call)
        elif data.startswith('file_'): file_control_callback(call)
        elif data.startswith('start_'): start_bot_callback(call)
        elif data.startswith('stop_'): stop_bot_callback(call)
        elif data.startswith('restart_'): restart_bot_callback(call)
        elif data.startswith('delete_'): delete_bot_callback(call)
        elif data.startswith('logs_'): logs_bot_callback(call)
        elif data == 'speed': speed_callback(call)
        elif data == 'back_to_main': back_to_main_callback(call)
        elif data.startswith('confirm_broadcast_'): handle_confirm_broadcast(call)
        elif data == 'cancel_broadcast': handle_cancel_broadcast(call)
        elif data == 'subscription': admin_required_callback(call, subscription_management_callback)
        elif data == 'stats': stats_callback(call)
        elif data == 'lock_bot': admin_required_callback(call, lock_bot_callback)
        elif data == 'unlock_bot': admin_required_callback(call, unlock_bot_callback)
        elif data == 'run_all_scripts': admin_required_callback(call, run_all_scripts_callback)
        elif data == 'broadcast': admin_required_callback(call, broadcast_init_callback) 
        elif data == 'admin_panel': admin_required_callback(call, admin_panel_callback)
        elif data == 'add_admin': owner_required_callback(call, add_admin_init_callback)
        elif data == 'remove_admin': owner_required_callback(call, remove_admin_init_callback) 
        elif data == 'list_admins': admin_required_callback(call, list_admins_callback)
        elif data == 'force_join_menu': admin_required_callback(call, force_join_menu_callback)
        elif data == 'add_force_ch': admin_required_callback(call, add_force_ch_init_callback)
        elif data == 'remove_force_ch': admin_required_callback(call, remove_force_ch_init_callback)
        elif data == 'list_force_ch': admin_required_callback(call, list_force_ch_callback)
        elif data == 'add_subscription': admin_required_callback(call, add_subscription_init_callback) 
        elif data == 'add_forever_subscription': admin_required_callback(call, add_forever_subscription_init_callback)
        elif data == 'remove_subscription': admin_required_callback(call, remove_subscription_init_callback) 
        elif data == 'check_subscription': admin_required_callback(call, check_subscription_init_callback) 
        elif data == 'owner_remove_menu': owner_remove_menu_callback(call)
        elif data.startswith('owner_select_'): owner_select_user_callback(call)
        else: bot.answer_callback_query(call.id, "Unknown action.")
    except Exception as e:
        logger.error(f"Error handling callback '{data}': {e}", exc_info=True)
        try: bot.answer_callback_query(call.id, "Error processing request.", show_alert=True)
        except Exception: pass

def admin_required_callback(call, func_to_run):
    if call.from_user.id not in admin_ids:
        bot.answer_callback_query(call.id, "⚠️ Admin permissions required.", show_alert=True)
        return
    func_to_run(call) 

def owner_required_callback(call, func_to_run):
    if call.from_user.id != OWNER_ID:
        bot.answer_callback_query(call.id, "⚠️ Owner permissions required.", show_alert=True)
        return
    func_to_run(call)

# --- 2-Step Python Upload Callbacks ---
def upload_callback(call):
    """Step 1: Show Python button"""
    user_id = call.from_user.id
    file_limit = get_user_file_limit(user_id)
    current_files = get_user_file_count(user_id)
    if current_files >= file_limit:
        bot.answer_callback_query(call.id, "⚠️ File limit reached. Delete files first.", show_alert=True)
        return
    bot.answer_callback_query(call.id)
    bot.edit_message_text(
        "📤 **Select Hosting Language:**\n\nClick the button below to proceed with Python hosting:",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=create_upload_category_markup(),
        parse_mode='Markdown'
    )

def upload_type_callback(call):
    """Step 2: User clicked Python -> Show 2 buttons: Choose Files or Choose Folder"""
    bot.answer_callback_query(call.id)
    bot.edit_message_text(
        "🐍 **Python Script Hosting**\n\nHow would you like to upload your Python project?",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=create_upload_mode_markup(),
        parse_mode='Markdown'
    )

def upload_mode_callback(call):
    """Step 3: User clicked Choose Files or Choose Folder -> Prompt to send .py or .zip"""
    mode = call.data.split('_')[-1] # 'file' or 'folder'
    user_id = call.from_user.id
    user_upload_state[user_id] = {'category': 'python', 'mode': mode}
    bot.answer_callback_query(call.id)

    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🔙 Back", callback_data="up_type_python"))

    if mode == 'file':
        prompt_text = (
            "📄 **Python -> Choose Files Selected**\n\n"
            "📤 Please send your `.py` script file now.\n"
            "It will be hosted and started automatically!"
        )
    else:
        prompt_text = (
            "📁 **Python -> Choose Folder Selected**\n\n"
            "📤 Please send your project folder compressed as a `.zip` file (with `main.py`/`bot.py`, `requirements.txt`, etc.).\n"
            "Dependencies will be installed and the bot will run automatically!"
        )

    bot.edit_message_text(
        prompt_text,
        call.message.chat.id,
        call.message.message_id,
        reply_markup=markup,
        parse_mode='Markdown'
    )
# --- End 2-Step Upload Callbacks ---

def check_files_callback(call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id 
    user_files_list = user_files.get(user_id, [])
    if not user_files_list:
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("📤 Upload New File", callback_data="upload"))
        markup.add(types.InlineKeyboardButton("🔙 Back to Main", callback_data='back_to_main'))
        bot.edit_message_text(
            "📂 **Your Hosted Python Files:**\n\n_(No Python scripts uploaded yet)_",
            chat_id, call.message.message_id, reply_markup=markup, parse_mode='Markdown'
        )
        return
    bot.answer_callback_query(call.id) 
    markup = types.InlineKeyboardMarkup(row_width=1) 
    for file_name, file_type in sorted(user_files_list): 
        is_running = is_bot_running(user_id, file_name)
        status_icon = "🟢 Running" if is_running else "🔴 Stopped"
        btn_text = f"🐍 {file_name} ({file_type.upper()}) - {status_icon}"
        markup.add(types.InlineKeyboardButton(btn_text, callback_data=f'file_{user_id}_{file_name}'))
    markup.add(types.InlineKeyboardButton("📤 Upload More", callback_data="upload"))
    markup.add(types.InlineKeyboardButton("🔙 Back to Main", callback_data='back_to_main'))
    bot.edit_message_text(
        f"📂 **Your Hosted Python Scripts ({len(user_files_list)}):**\n\n"
        f"👇 Click on any script or project below to manage it:",
        chat_id, call.message.message_id, reply_markup=markup, parse_mode='Markdown'
    )

def file_control_callback(call):
    try:
        _, script_owner_id_str, file_name = call.data.split('_', 2)
        script_owner_id = int(script_owner_id_str)
        requesting_user_id = call.from_user.id

        if not (requesting_user_id == script_owner_id or requesting_user_id in admin_ids):
            bot.answer_callback_query(call.id, "⚠️ You can only manage your own files.", show_alert=True)
            return

        user_files_list = user_files.get(script_owner_id, [])
        file_info = next((f for f in user_files_list if f[0] == file_name), None)
        if not file_info:
            bot.answer_callback_query(call.id, "⚠️ File not found.", show_alert=True)
            check_files_callback(call)
            return

        bot.answer_callback_query(call.id) 
        file_type = file_info[1]
        is_running = is_bot_running(script_owner_id, file_name)
        status_text = '🟢 Running' if is_running else '🔴 Stopped'
        bot.edit_message_text(
            f"⚙️ **Controls for:** `{file_name}` ({file_type.upper()})\n"
            f"👤 **Owner ID:** `{script_owner_id}`\n"
            f"📡 **Status:** {status_text}",
            call.message.chat.id, call.message.message_id,
            reply_markup=create_control_buttons(script_owner_id, file_name, is_running),
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"Error in file_control_callback: {e}", exc_info=True)
        bot.answer_callback_query(call.id, "An error occurred.", show_alert=True)

def start_bot_callback(call):
    try:
        _, script_owner_id_str, file_name = call.data.split('_', 2)
        script_owner_id = int(script_owner_id_str)
        requesting_user_id = call.from_user.id
        chat_id_for_reply = call.message.chat.id

        if not (requesting_user_id == script_owner_id or requesting_user_id in admin_ids):
            bot.answer_callback_query(call.id, "⚠️ Permission denied.", show_alert=True); return

        user_files_list = user_files.get(script_owner_id, [])
        file_info = next((f for f in user_files_list if f[0] == file_name), None)
        if not file_info:
            bot.answer_callback_query(call.id, "⚠️ File not found.", show_alert=True); return

        user_folder = get_user_folder(script_owner_id)
        file_path = os.path.join(user_folder, file_name)
        run_dir = os.path.dirname(file_path)

        if not os.path.exists(file_path):
            bot.answer_callback_query(call.id, f"⚠️ Error: File `{file_name}` missing!", show_alert=True)
            remove_user_file_db(script_owner_id, file_name); return

        if is_bot_running(script_owner_id, file_name):
            bot.answer_callback_query(call.id, f"⚠️ Script '{file_name}' already running.", show_alert=True)
            return

        bot.answer_callback_query(call.id, f"⏳ Starting {file_name}...")
        threading.Thread(target=run_script, args=(file_path, script_owner_id, run_dir, file_name, call.message)).start()

        time.sleep(1.5)
        is_now_running = is_bot_running(script_owner_id, file_name) 
        status_text = '🟢 Running' if is_now_running else '🟡 Starting'
        bot.edit_message_text(
            f"⚙️ **Controls for:** `{file_name}`\n👤 **User:** `{script_owner_id}`\n📡 **Status:** {status_text}",
            chat_id_for_reply, call.message.message_id,
            reply_markup=create_control_buttons(script_owner_id, file_name, is_now_running), parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"Error in start_bot_callback: {e}", exc_info=True)
        bot.answer_callback_query(call.id, "Error starting script.", show_alert=True)

def stop_bot_callback(call):
    try:
        _, script_owner_id_str, file_name = call.data.split('_', 2)
        script_owner_id = int(script_owner_id_str)
        requesting_user_id = call.from_user.id
        chat_id_for_reply = call.message.chat.id

        if not (requesting_user_id == script_owner_id or requesting_user_id in admin_ids):
            bot.answer_callback_query(call.id, "⚠️ Permission denied.", show_alert=True); return

        script_key = f"{script_owner_id}_{file_name}"
        bot.answer_callback_query(call.id, f"⏳ Stopping {file_name}...")
        process_info = bot_scripts.get(script_key)
        if process_info:
            kill_process_tree(process_info)
            if script_key in bot_scripts: del bot_scripts[script_key]

        bot.edit_message_text(
            f"⚙️ **Controls for:** `{file_name}`\n👤 **User:** `{script_owner_id}`\n📡 **Status:** 🔴 Stopped",
            chat_id_for_reply, call.message.message_id,
            reply_markup=create_control_buttons(script_owner_id, file_name, False), parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"Error in stop_bot_callback: {e}", exc_info=True)
        bot.answer_callback_query(call.id, "Error stopping script.", show_alert=True)

def restart_bot_callback(call):
    try:
        _, script_owner_id_str, file_name = call.data.split('_', 2)
        script_owner_id = int(script_owner_id_str)
        requesting_user_id = call.from_user.id
        chat_id_for_reply = call.message.chat.id

        if not (requesting_user_id == script_owner_id or requesting_user_id in admin_ids):
            bot.answer_callback_query(call.id, "⚠️ Permission denied.", show_alert=True); return

        user_files_list = user_files.get(script_owner_id, [])
        file_info = next((f for f in user_files_list if f[0] == file_name), None)
        if not file_info:
            bot.answer_callback_query(call.id, "⚠️ File not found.", show_alert=True); return

        user_folder = get_user_folder(script_owner_id)
        file_path = os.path.join(user_folder, file_name); script_key = f"{script_owner_id}_{file_name}"
        run_dir = os.path.dirname(file_path)

        bot.answer_callback_query(call.id, f"⏳ Restarting {file_name}...")
        if is_bot_running(script_owner_id, file_name):
            process_info = bot_scripts.get(script_key)
            if process_info: kill_process_tree(process_info)
            if script_key in bot_scripts: del bot_scripts[script_key]
            time.sleep(1) 

        threading.Thread(target=run_script, args=(file_path, script_owner_id, run_dir, file_name, call.message)).start()

        time.sleep(1.5) 
        is_now_running = is_bot_running(script_owner_id, file_name) 
        status_text = '🟢 Running' if is_now_running else '🟡 Starting'
        bot.edit_message_text(
            f"⚙️ **Controls for:** `{file_name}`\n👤 **User:** `{script_owner_id}`\n📡 **Status:** {status_text}",
            chat_id_for_reply, call.message.message_id,
            reply_markup=create_control_buttons(script_owner_id, file_name, is_now_running), parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"Error in restart_bot_callback: {e}", exc_info=True)
        bot.answer_callback_query(call.id, "Error restarting.", show_alert=True)

def delete_bot_callback(call):
    try:
        _, script_owner_id_str, file_name = call.data.split('_', 2)
        script_owner_id = int(script_owner_id_str)
        requesting_user_id = call.from_user.id
        chat_id_for_reply = call.message.chat.id

        if not (requesting_user_id == script_owner_id or requesting_user_id in admin_ids):
            bot.answer_callback_query(call.id, "⚠️ Permission denied.", show_alert=True); return

        bot.answer_callback_query(call.id, f"🗑️ Deleting {file_name}...")
        script_key = f"{script_owner_id}_{file_name}"
        if is_bot_running(script_owner_id, file_name):
            process_info = bot_scripts.get(script_key)
            if process_info: kill_process_tree(process_info)
            if script_key in bot_scripts: del bot_scripts[script_key]

        user_folder = get_user_folder(script_owner_id)
        if '/' in file_name:
            project_name = file_name.split('/')[0]
            project_folder = os.path.join(user_folder, project_name)
            if os.path.exists(project_folder) and os.path.isdir(project_folder):
                shutil.rmtree(project_folder)
        else:
            file_path = os.path.join(user_folder, file_name)
            log_path = os.path.join(user_folder, f"{os.path.splitext(file_name)[0]}.log")
            if os.path.exists(file_path): os.remove(file_path)
            if os.path.exists(log_path): os.remove(log_path)

        remove_user_file_db(script_owner_id, file_name)
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("🔙 Back to Files", callback_data="check_files"))
        bot.edit_message_text(
            f"🗑️ Script `{file_name}` has been deleted successfully!",
            chat_id_for_reply, call.message.message_id, reply_markup=markup, parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"Error in delete_bot_callback: {e}", exc_info=True)
        bot.answer_callback_query(call.id, "Error deleting.", show_alert=True)

def logs_bot_callback(call):
    try:
        _, script_owner_id_str, file_name = call.data.split('_', 2)
        script_owner_id = int(script_owner_id_str)
        requesting_user_id = call.from_user.id
        chat_id_for_reply = call.message.chat.id

        if not (requesting_user_id == script_owner_id or requesting_user_id in admin_ids):
            bot.answer_callback_query(call.id, "⚠️ Permission denied.", show_alert=True); return

        user_folder = get_user_folder(script_owner_id)
        file_path = os.path.join(user_folder, file_name)
        log_dir = os.path.dirname(file_path)
        log_path = os.path.join(log_dir, f"{os.path.splitext(os.path.basename(file_name))[0]}.log")
        if not os.path.exists(log_path):
            bot.answer_callback_query(call.id, "⚠️ No logs available.", show_alert=True); return

        bot.answer_callback_query(call.id) 
        file_size = os.path.getsize(log_path)
        max_log_kb = 100; max_tg_msg = 4096
        if file_size == 0: 
            log_content = "(Log empty)"
        elif file_size > max_log_kb * 1024:
             with open(log_path, 'rb') as f: f.seek(-max_log_kb * 1024, os.SEEK_END); log_bytes = f.read()
             log_content = f"(Last {max_log_kb} KB)\n...\n" + log_bytes.decode('utf-8', errors='ignore')
        else:
             with open(log_path, 'r', encoding='utf-8', errors='ignore') as f: log_content = f.read()

        if len(log_content) > max_tg_msg:
            log_content = log_content[-max_tg_msg:]

        bot.send_message(chat_id_for_reply, f"📜 Logs for `{file_name}`:\n```\n{log_content}\n```", parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Error in logs_bot_callback: {e}", exc_info=True)
        bot.answer_callback_query(call.id, "Error fetching logs.", show_alert=True)

def speed_callback(call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    start_cb_ping_time = time.time() 
    try:
        bot.edit_message_text("🏃 Testing speed...", chat_id, call.message.message_id)
        bot.send_chat_action(chat_id, 'typing') 
        response_time = round((time.time() - start_cb_ping_time) * 1000, 2)
        status = "🔓 Unlocked" if not bot_locked else "🔒 Locked"
        user_level, _ = get_user_status_and_expiry(user_id)
        speed_msg = (f"⚡ **Bot Speed & Status:**\n\n"
                     f"⏱️ **API Response Time:** `{response_time} ms`\n"
                     f"🚦 **Bot Status:** {status}\n"
                     f"👤 **Your Level:** {user_level}")
        bot.answer_callback_query(call.id) 
        bot.edit_message_text(speed_msg, chat_id, call.message.message_id, reply_markup=create_main_menu_inline(user_id), parse_mode='Markdown')
    except Exception:
         bot.answer_callback_query(call.id, "Error in speed test.", show_alert=True)

@bot.callback_query_handler(func=lambda call: call.data.startswith('owner_select_'))
def owner_select_user_callback(call):
    if call.from_user.id != OWNER_ID:
        bot.answer_callback_query(call.id, "⚠️ Owner only.", show_alert=True)
        return
    target_user_id = int(call.data.split('_')[2])
    bot.answer_callback_query(call.id)
    show_user_files_for_owner(call.message, target_user_id)

def back_to_main_callback(call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    user_upload_state.pop(user_id, None)
    file_limit = get_user_file_limit(user_id)
    current_files = get_user_file_count(user_id)
    limit_str = str(file_limit) if file_limit != float('inf') else "♾️ Unlimited"
    user_status, expiry_info = get_user_status_and_expiry(user_id)
    
    main_menu_text = (
        f"⚡✨ **𝐖𝐄𝐋𝐂𝐎𝐌𝐄 𝐁𝐀𝐂𝐊, {call.from_user.first_name}!** ✨⚡\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🆔 **User ID:** `{user_id}`\n"
        f"🔰 **Plan Status:** {user_status}{expiry_info}\n"
        f"📦 **Storage Used:** `{current_files} / {limit_str}`\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💎 _Tap **📤 Upload File** at the top of your menu to deploy!_"
    )
    try:
        bot.answer_callback_query(call.id)
        if call.message.content_type == 'photo':
            bot.delete_message(chat_id, call.message.message_id)
            bot.send_message(chat_id, main_menu_text, reply_markup=create_main_menu_inline(user_id), parse_mode='Markdown')
        else:
            bot.edit_message_text(main_menu_text, chat_id, call.message.message_id,
                                  reply_markup=create_main_menu_inline(user_id), parse_mode='Markdown')
    except Exception as e: 
        logger.error(f"Error handling back_to_main: {e}", exc_info=True)

def subscription_management_callback(call):
    bot.answer_callback_query(call.id)
    bot.edit_message_text("💳 **Subscription Management**\nSelect action:", call.message.chat.id, call.message.message_id, reply_markup=create_subscription_menu(), parse_mode='Markdown')

def stats_callback(call):
    bot.answer_callback_query(call.id)
    _logic_statistics(call.message) 
    try:
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=create_main_menu_inline(call.from_user.id))
    except Exception: 
        pass

def lock_bot_callback(call):
    global bot_locked; bot_locked = True
    bot.answer_callback_query(call.id, "🔒 Bot locked.")
    try: bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=create_main_menu_inline(call.from_user.id))
    except Exception: pass

def unlock_bot_callback(call):
    global bot_locked; bot_locked = False
    bot.answer_callback_query(call.id, "🔓 Bot unlocked.")
    try: bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=create_main_menu_inline(call.from_user.id))
    except Exception: pass

def run_all_scripts_callback(call):
    _logic_run_all_scripts(call)

@bot.callback_query_handler(func=lambda call: call.data == 'owner_remove_menu')
def owner_remove_menu_callback(call):
    if call.from_user.id != OWNER_ID:
        bot.answer_callback_query(call.id, "⚠️ Owner only.", show_alert=True)
        return
    bot.answer_callback_query(call.id)
    owner_remove_mode[OWNER_ID] = True
    bot.edit_message_text(
        "🔍 **REMOVE FILE MODE ACTIVATED**\n\nSend the **User ID** or **Username** (with @) of the person whose files you want to remove.\n\nType /cancel to exit.",
        chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode='Markdown'
    )

def broadcast_init_callback(call):
    bot.answer_callback_query(call.id)
    msg = bot.send_message(call.message.chat.id, "📢 Send message to broadcast.\n/cancel to abort.")
    bot.register_next_step_handler(msg, process_broadcast_message)

def process_broadcast_message(message):
    user_id = message.from_user.id
    if user_id not in admin_ids: bot.reply_to(message, "⚠️ Not authorized."); return
    if message.text and message.text.lower() == '/cancel': bot.reply_to(message, "Broadcast cancelled."); return

    broadcast_content = message.text
    if not broadcast_content and not (message.photo or message.video or message.document or message.sticker or message.voice or message.audio):
         bot.reply_to(message, "⚠️ Cannot broadcast empty message.")
         return

    target_count = len(active_users)
    markup = types.InlineKeyboardMarkup()
    markup.row(types.InlineKeyboardButton("✅ Confirm & Send", callback_data=f"confirm_broadcast_{message.message_id}"),
               types.InlineKeyboardButton("❌ Cancel", callback_data="cancel_broadcast"))

    preview_text = broadcast_content[:1000].strip() if broadcast_content else "(Media message)"
    bot.reply_to(message, f"⚠️ Confirm Broadcast to **{target_count}** users:\n\n```\n{preview_text}\n```", reply_markup=markup, parse_mode='Markdown')

def handle_confirm_broadcast(call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    if user_id not in admin_ids: bot.answer_callback_query(call.id, "⚠️ Admin only.", show_alert=True); return
    try:
        original_message = call.message.reply_to_message
        if not original_message: raise ValueError("Original message not found.")

        broadcast_text = original_message.text if original_message.text else None
        broadcast_photo_id = original_message.photo[-1].file_id if original_message.photo else None
        broadcast_video_id = original_message.video.file_id if original_message.video else None

        bot.answer_callback_query(call.id, "🚀 Starting broadcast...")
        bot.edit_message_text("📢 Broadcasting...", chat_id, call.message.message_id, reply_markup=None)
        thread = threading.Thread(target=execute_broadcast, args=(
            broadcast_text, broadcast_photo_id, broadcast_video_id, 
            original_message.caption if (broadcast_photo_id or broadcast_video_id) else None, chat_id))
        thread.start()
    except Exception:
        bot.edit_message_text("❌ Error starting broadcast.", chat_id, call.message.message_id, reply_markup=None)

def handle_cancel_broadcast(call):
    bot.answer_callback_query(call.id, "Broadcast cancelled.")
    bot.delete_message(call.message.chat.id, call.message.message_id)
    if call.message.reply_to_message:
        try: bot.delete_message(call.message.chat.id, call.message.reply_to_message.message_id)
        except: pass

def execute_broadcast(broadcast_text, photo_id, video_id, caption, admin_chat_id):
    sent_count = 0; failed_count = 0; blocked_count = 0
    start_exec_time = time.time() 
    users_to_broadcast = list(active_users); total_users = len(users_to_broadcast)
    batch_size = 25; delay_batches = 1.5

    for i, user_id_bc in enumerate(users_to_broadcast):
        try:
            if broadcast_text: bot.send_message(user_id_bc, broadcast_text, parse_mode='Markdown')
            elif photo_id: bot.send_photo(user_id_bc, photo_id, caption=caption, parse_mode='Markdown' if caption else None)
            elif video_id: bot.send_video(user_id_bc, video_id, caption=caption, parse_mode='Markdown' if caption else None)
            sent_count += 1
        except telebot.apihelper.ApiTelegramException as e:
            err_desc = str(e).lower()
            if any(s in err_desc for s in ["bot was blocked", "user is deactivated", "chat not found", "kicked"]): 
                blocked_count += 1
            else: 
                failed_count += 1
        except Exception: 
            failed_count += 1

        if (i + 1) % batch_size == 0 and i < total_users - 1:
            time.sleep(delay_batches)

    duration = round(time.time() - start_exec_time, 2)
    result_msg = (f"📢 Broadcast Complete!\n\n✅ Sent: {sent_count}\n❌ Failed: {failed_count}\n"
                  f"🚫 Blocked: {blocked_count}\n⏱️ Duration: {duration}s")
    try: bot.send_message(admin_chat_id, result_msg)
    except Exception: pass

def admin_panel_callback(call):
    bot.answer_callback_query(call.id)
    bot.edit_message_text("👑 **Admin Panel**\nManage admins and Force-Join unlock links:", call.message.chat.id, call.message.message_id, reply_markup=create_admin_panel(), parse_mode='Markdown')

def add_admin_init_callback(call):
    bot.answer_callback_query(call.id)
    msg = bot.send_message(call.message.chat.id, "👑 Enter User ID to promote to Admin:\n/cancel to abort.")
    bot.register_next_step_handler(msg, process_add_admin_id)

def process_add_admin_id(message):
    if message.from_user.id != OWNER_ID: bot.reply_to(message, "⚠️ Owner only."); return
    if message.text.lower() == '/cancel': bot.reply_to(message, "Cancelled."); return
    try:
        new_admin_id = int(message.text.strip())
        add_admin_db(new_admin_id) 
        bot.reply_to(message, f"✅ User `{new_admin_id}` promoted to Admin.", parse_mode='Markdown')
    except Exception:
        bot.reply_to(message, "⚠️ Invalid ID.")

def remove_admin_init_callback(call):
    bot.answer_callback_query(call.id)
    msg = bot.send_message(call.message.chat.id, "👑 Enter User ID of Admin to remove:\n/cancel to abort.")
    bot.register_next_step_handler(msg, process_remove_admin_id)

def process_remove_admin_id(message):
    if message.from_user.id != OWNER_ID: bot.reply_to(message, "⚠️ Owner only."); return
    if message.text.lower() == '/cancel': bot.reply_to(message, "Cancelled."); return
    try:
        admin_id_remove = int(message.text.strip())
        if remove_admin_db(admin_id_remove): 
            bot.reply_to(message, f"✅ Admin `{admin_id_remove}` removed.", parse_mode='Markdown')
        else: 
            bot.reply_to(message, "❌ Failed to remove admin.")
    except Exception:
        bot.reply_to(message, "⚠️ Invalid ID.")

def list_admins_callback(call):
    bot.answer_callback_query(call.id)
    admin_list_str = "\n".join(f"- `{aid}` {'(Owner)' if aid == OWNER_ID else ''}" for aid in sorted(list(admin_ids)))
    bot.edit_message_text(f"👑 **Current Admins:**\n\n{admin_list_str}", call.message.chat.id, call.message.message_id, reply_markup=create_admin_panel(), parse_mode='Markdown')

# --- Admin Force-Join (Join to Unlock) Link Management ---
def force_join_menu_callback(call):
    bot.answer_callback_query(call.id)
    text = (
        "🔗 **Force Join (Join to Unlock) Channel Manager**\n\n"
        f"📡 **Active Channels:** `{len(force_channels)}`\n"
        "⚠️ _Note: Make sure the bot is an **Admin** inside any channel you add so it can verify if users joined!_"
    )
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=create_force_join_panel(), parse_mode='Markdown')

def add_force_ch_init_callback(call):
    bot.answer_callback_query(call.id)
    prompt = (
        "➕ **Add Force-Join Channel & Link**\n\n"
        "Send the channel details in one of these formats:\n"
        "1️⃣ For public channels:\n`@channelusername`\n\n"
        "2️⃣ For private channels (or custom invite link):\n`-100123456789 | https://t.me/+InviteHash`\n"
        "or\n`@channelusername | https://t.me/channelusername`\n\n"
        "Type `/cancel` to abort."
    )
    msg = bot.send_message(call.message.chat.id, prompt, parse_mode='Markdown')
    bot.register_next_step_handler(msg, process_add_force_channel)

def process_add_force_channel(message):
    if message.from_user.id not in admin_ids: return
    if message.text and message.text.lower() == '/cancel':
        bot.reply_to(message, "❌ Cancelled.")
        return
    raw = message.text.strip()
    if '|' in raw:
        parts = [p.strip() for p in raw.split('|', 1)]
        ch_id, inv_link = parts[0], parts[1]
    else:
        if raw.startswith('https://t.me/') and not raw.startswith('https://t.me/+'):
            uname = raw.split('https://t.me/')[-1].split('/')[0].strip()
            ch_id = f"@{uname}"
            inv_link = f"https://t.me/{uname}"
        elif raw.startswith('@'):
            ch_id = raw
            inv_link = f"https://t.me/{raw.lstrip('@')}"
        else:
            bot.reply_to(message, "⚠️ Invalid format! Use `@username` or `-100123456789 | https://t.me/+link`", parse_mode='Markdown')
            return

    add_force_channel_db(ch_id, inv_link)
    bot.reply_to(
        message,
        f"✅ **Force-Join Channel Added!**\n\n🆔 **Channel:** `{ch_id}`\n🔗 **Link:** {inv_link}\n\n_(Make sure the bot is promoted to Admin in that channel)_",
        parse_mode='Markdown'
    )

def remove_force_ch_init_callback(call):
    bot.answer_callback_query(call.id)
    if not force_channels:
        bot.send_message(call.message.chat.id, "ℹ️ No Force-Join channels are currently set.")
        return
    ch_list = "\n".join(f"• `{k}` → {v}" for k, v in force_channels.items())
    msg = bot.send_message(
        call.message.chat.id,
        f"➖ **Remove Force-Join Channel**\n\nCurrent Channels:\n{ch_list}\n\nSend the exact Channel ID / `@username` to remove (or `all` to clear all):\n/cancel to abort.",
        parse_mode='Markdown'
    )
    bot.register_next_step_handler(msg, process_remove_force_channel)

def process_remove_force_channel(message):
    if message.from_user.id not in admin_ids: return
    if message.text and message.text.lower() == '/cancel':
        bot.reply_to(message, "❌ Cancelled.")
        return
    target = message.text.strip()
    if target.lower() == 'all':
        for k in list(force_channels.keys()):
            remove_force_channel_db(k)
        bot.reply_to(message, "✅ Cleared all Force-Join channels.")
        return
    if remove_force_channel_db(target):
        bot.reply_to(message, f"✅ Removed `{target}` from Force-Join list.", parse_mode='Markdown')
    else:
        bot.reply_to(message, f"❌ Channel `{target}` not found.", parse_mode='Markdown')

def list_force_ch_callback(call):
    bot.answer_callback_query(call.id)
    if not force_channels:
        text = "📋 **Active Force-Join Channels:**\n\n_(None configured — bot is unlocked for everyone)_"
    else:
        lines = [f"• `{ch}` → [Invite Link]({lnk})" for ch, lnk in force_channels.items()]
        text = "📋 **Active Force-Join Channels:**\n\n" + "\n".join(lines)
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=create_force_join_panel(), parse_mode='Markdown', disable_web_page_preview=True)

# --- Subscription Callbacks (With Forever Subscription Support) ---
def add_subscription_init_callback(call):
    bot.answer_callback_query(call.id)
    msg = bot.send_message(
        call.message.chat.id,
        "💳 **Add Subscription**\n\nEnter User ID & Days (e.g., `12345678 30`) or type `12345678 forever` for Lifetime:\n/cancel to abort.",
        parse_mode='Markdown'
    )
    bot.register_next_step_handler(msg, process_add_subscription_details)

def process_add_subscription_details(message):
    if message.from_user.id not in admin_ids: bot.reply_to(message, "⚠️ Not authorized."); return
    if message.text.lower() == '/cancel': bot.reply_to(message, "Cancelled."); return
    try:
        parts = message.text.split()
        sub_user_id = int(parts[0].strip())
        duration_arg = parts[1].strip().lower()
        if duration_arg in ['forever', 'lifetime', 'inf']:
            save_subscription(sub_user_id, 'forever')
            bot.reply_to(message, f"♾️ **Forever (Lifetime) Subscription** activated for user `{sub_user_id}`!", parse_mode='Markdown')
        else:
            days = int(duration_arg)
            current_expiry = user_subscriptions.get(sub_user_id, {}).get('expiry')
            start_date_new_sub = datetime.now()
            if isinstance(current_expiry, datetime) and current_expiry > start_date_new_sub:
                start_date_new_sub = current_expiry
            new_expiry = start_date_new_sub + timedelta(days=days)
            save_subscription(sub_user_id, new_expiry)
            bot.reply_to(message, f"✅ Subscription added for `{sub_user_id}` for **{days} days**.", parse_mode='Markdown')
    except Exception:
        bot.reply_to(message, "⚠️ Invalid format. Use: `ID days` or `ID forever`", parse_mode='Markdown')

def add_forever_subscription_init_callback(call):
    bot.answer_callback_query(call.id)
    msg = bot.send_message(
        call.message.chat.id,
        "♾️ **Add Forever (Lifetime) Subscription**\n\nSend the **User ID** to grant permanent premium access:\n/cancel to abort.",
        parse_mode='Markdown'
    )
    bot.register_next_step_handler(msg, process_add_forever_subscription)

def process_add_forever_subscription(message):
    if message.from_user.id not in admin_ids: bot.reply_to(message, "⚠️ Not authorized."); return
    if message.text.lower() == '/cancel': bot.reply_to(message, "Cancelled."); return
    try:
        sub_user_id = int(message.text.strip())
        save_subscription(sub_user_id, 'forever')
        bot.reply_to(message, f"♾️✨ **Forever Subscription Granted!**\nUser `{sub_user_id}` now has Lifetime Premium access.", parse_mode='Markdown')
    except Exception:
        bot.reply_to(message, "⚠️ Invalid User ID.")

def remove_subscription_init_callback(call):
    bot.answer_callback_query(call.id)
    msg = bot.send_message(call.message.chat.id, "💳 Enter User ID to remove subscription:\n/cancel to abort.")
    bot.register_next_step_handler(msg, process_remove_subscription_id)

def process_remove_subscription_id(message):
    if message.from_user.id not in admin_ids: bot.reply_to(message, "⚠️ Not authorized."); return
    if message.text.lower() == '/cancel': bot.reply_to(message, "Cancelled."); return
    try:
        sub_user_id_remove = int(message.text.strip())
        remove_subscription_db(sub_user_id_remove) 
        bot.reply_to(message, f"✅ Subscription removed for `{sub_user_id_remove}`.", parse_mode='Markdown')
    except Exception:
        bot.reply_to(message, "⚠️ Invalid ID.")

def check_subscription_init_callback(call):
    bot.answer_callback_query(call.id)
    msg = bot.send_message(call.message.chat.id, "💳 Enter User ID to check subscription:\n/cancel to abort.")
    bot.register_next_step_handler(msg, process_check_subscription_id)

def process_check_subscription_id(message):
    if message.from_user.id not in admin_ids: bot.reply_to(message, "⚠️ Not authorized."); return
    if message.text.lower() == '/cancel': bot.reply_to(message, "Cancelled."); return
    try:
        sub_user_id_check = int(message.text.strip())
        if sub_user_id_check in user_subscriptions:
            expiry_dt = user_subscriptions[sub_user_id_check].get('expiry')
            if expiry_dt == 'forever':
                bot.reply_to(message, f"♾️ User `{sub_user_id_check}` has an active **Forever (Lifetime)** subscription!", parse_mode='Markdown')
            elif isinstance(expiry_dt, datetime) and expiry_dt > datetime.now():
                bot.reply_to(message, f"✅ Active subscription until: `{expiry_dt:%Y-%m-%d %H:%M:%S}`", parse_mode='Markdown')
            else:
                bot.reply_to(message, "⚠️ Subscription expired.")
        else: 
            bot.reply_to(message, "ℹ️ No active subscription found.")
    except Exception:
        bot.reply_to(message, "⚠️ Invalid ID.")

bot.callback_query_handler(func=lambda call: True)(handle_callbacks)

# --- Cleanup Function ---
def cleanup():
    logger.warning("Shutdown. Cleaning up processes...")
    for key in list(bot_scripts.keys()):
        kill_process_tree(bot_scripts[key])
atexit.register(cleanup)

# ============ REMOVE FILE COMMANDS ============
@bot.message_handler(commands=['removefile'])
def removefile_button(message):
    if message.from_user.id == OWNER_ID:
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton('🗑️ REMOVE FILE', callback_data='owner_remove_menu'))
        bot.send_message(message.chat.id, "🗑️ **Remove File Menu**", reply_markup=markup, parse_mode='Markdown')
    else:
        bot.reply_to(message, "⚠️ Only Owner can use this command.")

@bot.message_handler(commands=['remove', 'delete', 'rm'])
def direct_remove_command(message):
    if message.from_user.id != OWNER_ID:
        bot.reply_to(message, "⚠️ Only Owner can use this command.")
        return
    owner_remove_mode[OWNER_ID] = True
    bot.reply_to(message, "🔍 **REMOVE FILE MODE ACTIVATED**\n\nSend User ID or Username:", parse_mode='Markdown')

@bot.message_handler(commands=['commands', 'cmds', 'cmd'])
def show_commands(message):
    commands_text = (
        "┌────────────────────────────────────┐\n"
        "│         📋 BOT COMMANDS            │\n"
        "├────────────────────────────────────┤\n"
        "│  📤 TOP FILE COMMANDS              │\n"
        "│  /upload   → Upload Python Script  │\n"
        "│  /files    → Check your files      │\n"
        "├────────────────────────────────────┤\n"
        "│  📢 GENERAL COMMANDS               │\n"
        "│  /start    → Start the bot         │\n"
        "│  /help     → Show this help        │\n"
        "│  /ping     → Check bot latency     │\n"
        "│  /status   → Bot statistics        │\n"
        "│  /speed    → Check bot speed       │\n"
        "└────────────────────────────────────┘"
    )
    bot.reply_to(message, f"<pre>{commands_text}</pre>", parse_mode='HTML')

# --- Main Execution ---
if __name__ == '__main__':
    logger.info("="*40 + "\n🤖 Bot Starting Up...\n" + "="*40)
    keep_alive()
    logger.info("🚀 Starting polling...")
    while True:
        try:
            bot.infinity_polling(logger_level=logging.INFO, timeout=60, long_polling_timeout=30)
        except requests.exceptions.ReadTimeout: time.sleep(5)
        except requests.exceptions.ConnectionError: time.sleep(15)
        except Exception as e:
            logger.critical(f"💥 Critical polling error: {e}", exc_info=True)
            time.sleep(30)
        finally: time.sleep(1)
