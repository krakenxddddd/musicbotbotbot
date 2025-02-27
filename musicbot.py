from highrise import *
from highrise.models import *
import os
import subprocess
import asyncio
import sqlite3
import json
import time
import aiohttp
import shutil
from yandex_music import ClientAsync, Track
import logging
from urllib.parse import urlparse
import json # Import the json library for queue persistence
import random
from highrise.__main__ import BotDefinition, main, import_module, arun

# Настройка логов
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("YandexMusicBot")

# Конфигурация
YANDEX_TOKEN = "y0__xCToNy2Bxje-AYgzoKAqhI4KZ5ZDaz1AkEnFYMkih4HYbnBag"  # Замените на реальный токен
DB_PATH = "musicbot.db"
DOWNLOAD_DIR = "downloads"
ICECAST_URL = "icecast://sadfsdafdsa_sdafasdfasd:teenparalich0@live.radioking.com:80/kraken-radioooo"  # Настройте под ваш Icecast

bot_file_name = "musicbot"
bot_class_name = "YandexMusicBot"
room_id = "67372d6e6c5bb6d658b48c8a"
bot_token = "16cdf17cd22e24df641e053066713cd0245a54747532b122ee7634a25194a0fa"

if __name__ == "__main__":
    definitions = [
            BotDefinition(
                getattr(import_module(bot_file_name), bot_class_name)(),
                room_id, bot_token)
    ]

    while True:
        try:
            arun(main(definitions))
        except (aiohttp.client_exceptions.ClientConnectionResetError, Exception) as e:  # Перехватываем конкретную ошибку и общую Exception
            import traceback
            print("Caught an exception:")
            traceback.print_exc()
            print("Restarting bot...")
            time.sleep(5)  # Пауза перед перезапуском
            continue


class YandexMusicBot(BaseBot):
    def __init__(self):
        super().__init__()
        self.conn = sqlite3.connect(DB_PATH)
        self.cursor = self.conn.cursor()
        self.init_db()
        self.setup_dirs()
        
        # Состояние бота
        self.song_queue = []
        self.currently_playing = False
        self.skip_event = asyncio.Event()
        self.ffmpeg_process = None
        self.current_song = None
        self.admins = {'fedorballz', 'Skara0'}
        self.play_lock = asyncio.Lock()
        self.play_task = None
        self.play_event = asyncio.Event()
        self.current_position_ms = 0
        self.start_time_ms = None
        self.stream_stop_event = asyncio.Event()
        self.request_queue = asyncio.Queue()
        self.request_lock = asyncio.Lock()
        self.client = None

        self.messages_dict_dj = {
            "greeting1": "\n🎵 Включаем музыку! Добро пожаловать к нашему DJ-боту!",
            "greeting2": "\n🎶 Привет! Используй /play [название] или /linkplay [ссылка]",
            "greeting3": "\n🎤 DJ-бот в сети! Заказывай любимые песни!",
            "balance_reminder": "\n💰 Проверь баланс: /bal | Стоимость трека: 10 голды",
            "tip_reminder": "\n✨ Поддержи бота чаевыми от 10г и заказывай треки",
            "command_list": "\nКоманды:\n/play [название]\n/linkplay [ссылка]",
            "command_list2": "\nКоманды:\n/skip | /bal | /np | /q"
        }

    def init_db(self):
        self.cursor.execute('''CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,
            balance INTEGER DEFAULT 10)''')
        self.cursor.execute('''CREATE TABLE IF NOT EXISTS queue_data (
            id INTEGER PRIMARY KEY,
            data TEXT)''')
        self.conn.commit()

    def setup_dirs(self):
        os.makedirs(DOWNLOAD_DIR, exist_ok=True)

    async def on_start(self, session_metadata: SessionMetadata) -> None:
        await self.connect_yandex()
        await self.load_queue()
        asyncio.create_task(self.repeat_jackpot_rules())
        await self.highrise.walk_to(Position(16.5, 0.0, 20.5))
        logger.info("Bot started")
        self.play_task = asyncio.create_task(self.playback_loop())

    async def connect_yandex(self):
        try:
            self.client = await ClientAsync(YANDEX_TOKEN).init()
            logger.info("Успешное подключение к Яндекс.Музыке")
        except Exception as e:
            logger.error(f"Ошибка подключения: {e}")

    async def on_user_join(self, user: User, position: Position) -> None:
        await self.highrise.send_whisper(user.id, self.messages_dict_dj["command_list"])
        await self.highrise.send_whisper(user.id, self.messages_dict_dj["command_list2"])
        self.add_user_to_db(user.username)

    def add_user_to_db(self, username):
        try:
            self.cursor.execute("INSERT OR IGNORE INTO users (username) VALUES (?)", (username,))
            self.conn.commit()
        except sqlite3.Error as e:
            logger.error(f"DB error: {e}")

    async def on_tip(self, sender: User, receiver: User, tip: CurrencyItem) -> None:
        if receiver.username == "KrakenDJ":
            self.update_user_balance(sender.username, tip.amount)
            await self.highrise.chat(f"@{sender.username} пополнил баланс на {tip.amount}г!")

    def get_user_balance(self, username):
        try:
            self.cursor.execute("SELECT balance FROM users WHERE username = ?", (username,))
            result = self.cursor.fetchone()
            return result[0] if result else 0
        except sqlite3.Error as e:
            logger.error(f"DB error: {e}")
            return 0

    def update_user_balance(self, username, amount):
        try:
            self.cursor.execute("UPDATE users SET balance = balance + ? WHERE username = ?", (amount, username))
            self.conn.commit()
        except sqlite3.Error as e:
            logger.error(f"DB error: {e}")

    async def download_yandex_audio(self, query: str, search_by_title: bool = True) -> tuple:
        try:
            if search_by_title:
                search_result = await self.client.search(query, type_='track')
                if not search_result.tracks:
                    return None, None, 0, False
                track = search_result.tracks.results[0]
            else:
                track_id = self.parse_track_id(query)
                if not track_id:
                    return None, None, 0, False
                track = (await self.client.tracks(track_id))[0]

            download_info = await track.get_download_info(get_direct_links=True)
            best_link = max([d for d in download_info if d.codec == 'mp3'],
                          key=lambda x: x.bitrate_in_kbps)
            
            file_path = f"{DOWNLOAD_DIR}/{track.id}.mp3"
            await self.download_file(best_link.direct_link, file_path)
            
            return (
                file_path,
                f"{track.title} - {', '.join(a.name for a in track.artists)}",
                track.duration_ms // 1000,
                False
            )
        except Exception as e:
            logger.error(f"Ошибка загрузки: {e}")
            return None, None, 0, False

    async def download_file(self, url: str, path: str):
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                with open(path, 'wb') as f:
                    async for chunk in resp.content.iter_chunked(1024):
                        f.write(chunk)

    def parse_track_id(self, url: str) -> int:
        try:
            path = urlparse(url).path
            return int(path.split('/')[-1])
        except:
            return None

    async def on_chat(self, user: User, message: str) -> None:
        try:
            if message.startswith('/play '):
                query = message[6:].strip()
                balance = self.get_user_balance(user.username)
                if balance < 10:
                    await self.highrise.send_whisper(user.id, "❌ Недостаточно средств! Нужно 10г")
                    return
                
                await self.add_to_queue(query, user.username, search_by_title=True)
            if message.startswith('/cash'):
                if user.username not in allowed_usernames:
                    await self.highrise.send_whisper(user.id, "\n❌ Это команда тебе не доступна!")
                    return
                parts = message.split()
                if len(parts) > 2:  # Check if username and amount are provided
                    target_username = parts[1].replace("@", "")
                    try:
                        amount = int(parts[2])
                        if amount > 0:
                            self.update_user_balance(target_username, amount)
                            await self.highrise.send_whisper(user.id, f"Выдал {amount} @{target_username} на баланс")
                        else:
                            await self.highrise.send_whisper(user.id, "Сумма должна быть положительной.")
                    except ValueError:
                        await self.highrise.send_whisper(user.id, "Неверная сумма. Пожалуйста, введите число.")
                    except Exception as e:
                        await self.highrise.send_whisper(user.id, f"Ошибка при добавлении на баланс @{target_username}: {e}")
                else:
                    await self.highrise.send_whisper(user.id, "Используй: /cash @username amount") #Correct usage
            
            elif message.startswith('/linkplay '):
                url = message[10:].strip()
                balance = self.get_user_balance(user.username)
                if balance < 10:
                    await self.highrise.send_whisper(user.id, "❌ Недостаточно средств! Нужно 10г")
                    return
                
                await self.add_to_queue(url, user.username, search_by_title=False)
            
            elif message.startswith('/skip'):
                await self.skip_song(user)
            
            elif message.startswith('/bal'):
                balance = self.get_user_balance(user.username)
                await self.highrise.send_whisper(user.id, f"💰 Ваш баланс: {balance}г")
            
            elif message.startswith('/np'):
                await self.now_playing()
            
            elif message.startswith('/q'):
                await self.check_queue()

        except Exception as e:
            logger.error(f"Chat error: {e}")

    async def add_to_queue(self, song_request, owner, search_by_title=True):
        file_path, title, duration, is_playlist = await self.download_yandex_audio(song_request, search_by_title)
        
        if not file_path:
            await self.highrise.chat(f"❌ Не удалось найти трек | @{owner}")
            return
        
        if duration > 240:
            await self.highrise.chat(f"@{owner} трек слишком длинный (макс 4 мин)")
            os.remove(file_path)
            return
        
        self.song_queue.append({
            'title': title,
            'file_path': file_path,
            'owner': owner,
            'duration': duration
        })
        
        await self.highrise.chat(f"🎵 Добавлено: {title}\n👤 @{owner} | ⏱ {duration//60}:{duration%60:02}")
        self.update_user_balance(owner, -10)
        await self.save_queue()

    async def playback_loop(self):
        while True:
            if self.song_queue and not self.currently_playing:
                self.currently_playing = True
                song = self.song_queue.pop(0)
                await self.stream_track(song['file_path'])
                self.currently_playing = False
                os.remove(song['file_path'])
                await self.save_queue()
            await asyncio.sleep(1)

    async def stream_track(self, file_path: str):
        cmd = [
            'ffmpeg', '-re', '-i', file_path,
            '-f', 'mp3', '-acodec', 'libmp3lame',
            '-ab', '192k', '-ar', '44100',
            ICECAST_URL
        ]
        
        try:
            self.ffmpeg_process = await asyncio.create_subprocess_exec(*cmd)
            await self.ffmpeg_process.wait()
        except Exception as e:
            logger.error(f"Ошибка стрима: {e}")
        finally:
            if self.ffmpeg_process:
                self.ffmpeg_process.terminate()

    async def skip_song(self, user: User):
        if user.username in self.admins or user.username == self.current_song.get('owner'):
            if self.ffmpeg_process:
                self.ffmpeg_process.terminate()
                await self.highrise.chat(f"⏩ @{user.username} пропустил трек")
        else:
            await self.highrise.send_whisper(user.id, "❌ Нет прав для пропуска!")

    async def check_queue(self, page_number=1):
        queue_msg = "🎧 Очередь:\n" + "\n".join(
            f"{i+1}. {song['title']} (@{song['owner']})" 
            for i, song in enumerate(self.song_queue[:5])
        )
        await self.highrise.chat(queue_msg if self.song_queue else "Очередь пуста")

    async def now_playing(self):
        if self.current_song:
            msg = f"▶️ Сейчас играет: {self.current_song['title']}\n👤 @{self.current_song['owner']}"
            await self.highrise.chat(msg)
        else:
            await self.highrise.chat("Сейчас ничего не играет")

    async def repeat_jackpot_rules(self):
        while True:
            for msg in self.messages_dict_dj.values():
                await self.highrise.chat(msg)
                await asyncio.sleep(60)

    async def load_queue(self):
        self.cursor.execute("SELECT data FROM queue_data LIMIT 1")
        data = self.cursor.fetchone()
        if data:
            self.song_queue = json.loads(data[0])

    async def save_queue(self):
        data = json.dumps(self.song_queue)
        self.cursor.execute("INSERT OR REPLACE INTO queue_data (id, data) VALUES (1, ?)", (data,))
        self.conn.commit()

    async def on_close(self):
        await self.save_queue()
        self.conn.close()
        if self.ffmpeg_process:
            self.ffmpeg_process.terminate()

allowed_usernames = ["fedorballz", "Skara0"]
