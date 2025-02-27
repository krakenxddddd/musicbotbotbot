from highrise import *
from highrise.models import *
import os
import subprocess
from highrise import BaseBot, User
import asyncio
import shutil
from highrise.__main__ import BotDefinition, main, import_module, arun
import time
import sqlite3
import json
import aiohttp
from yandex_music import ClientAsync
import logging
from urllib.parse import urlparse

# Настройка логов
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("YandexMusicBot")

# Конфигурация
YANDEX_TOKEN = "y0__xCToNy2Bxje-AYgzoKAqhI4KZ5ZDaz1AkEnFYMkih4HYbnBag"  # Замените на реальный токен
DB_PATH = "musicbot.db"
DOWNLOAD_DIR = "downloads"
ICECAST_URL = "icecast://sadfsdafdsa_sdafasdfasd:teenparalich0@live.radioking.com:80/kraken-radioooo"

# BOT SETTINGS
bot_file_name = "musicbot"
bot_class_name = "xenoichi"
room_id = "67372d6e6c5bb6d658b48c8a"
bot_token = "16cdf17cd22e24df641e053066713cd0245a54747532b122ee7634a25194a0fa"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,
            balance INTEGER DEFAULT 10
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS queue_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            queue TEXT
        )
    ''')
    conn.commit()
    conn.close()

init_db()

if __name__ == "__main__":
    definitions = [
        BotDefinition(
            getattr(import_module(bot_file_name), bot_class_name)(),
            room_id, bot_token)
    ]

    while True:
        try:
            arun(main(definitions))
        except (aiohttp.client_exceptions.ClientConnectionResetError, Exception) as e:
            import traceback
            print("Caught an exception:")
            traceback.print_exc()
            print("Restarting bot...")
            time.sleep(5)
            continue

class xenoichi(BaseBot):
    def __init__(self):
        super().__init__()
        self.conn = sqlite3.connect(DB_PATH)
        self.cursor = self.conn.cursor()
        self.song_queue = []
        self.currently_playing = False
        self.skip_event = asyncio.Event()
        self.ffmpeg_process = None
        self.current_song = None
        self.admins = {'fedorballz', 'Skara0'}
        self.ready = asyncio.Event()
        self.play_lock = asyncio.Lock()
        self.play_task = None
        self.play_event = asyncio.Event()
        self.current_position_ms = 0
        self.start_time_ms = None
        self.stream_stop_event = asyncio.Event()
        self.request_queue = asyncio.Queue()
        self.request_lock = asyncio.Lock()
        self.client = None # Yandex Music Client

    messages_dict_dj = {
            "greeting1": "\n🎵 Включаем музыку!  Добро пожаловать к нашему DJ-боту!\n\n🎧 Закажи песню командой /play [название песни] или /linkplay [ссылка Yandex Music].\n",
            "greeting2": "\n🎶 Привет! \n\n🎵 Используй команду /play [название песни] или /linkplay [ссылка Yandex Music], чтобы добавить трек в очередь.\n",
            "greeting3": "\n🎤 DJ-бот в сети!  Заказывай свои любимые песни!\n\n🎶 Отправь чаевые, чтобы пополнить баланс и заказать песню\n",
            "balance_reminder": "\n💰 Проверь свой баланс командой /bal\n\n🎧 Не забудь, что за каждую песню списывается 10 голды!\n",
            "tip_reminder": "\n✨ Хочешь поддержать бота?  Отправь чаевые в размере 10г и закажи песню\n\n🎵 /play [название песни] или /linkplay [ссылка Yandex Music]\n",
            "command_list": "\nСписок команд:\n\n/play [название песни] - Заказать песню по названию\n/linkplay [ссылка] - Заказать песню по ссылке Yandex Music",
            "command_list2": "\nСписок команд:\n\n/skip - пропустить свой трек\n/bal - Проверить баланс\n/np - Узнать название трека\n/q - узнать очередь\n\nОтправь чаевые, чтобы пополнить баланс"
        }


    async def repeat_jackpot_rules(self):
        messages = list(self.messages_dict_dj.values())
        message_index = 0
        while True:
            message = messages[message_index]
            try:
                await self.highrise.chat(message)
            except aiohttp.client_exceptions.ClientConnectionResetError as e:
                print(f"Connection reset error in repeat_jackpot_rules, retrying in 10 seconds: {e}")
                await asyncio.sleep(10)  # Retry after a delay
                continue
            except Exception as e:
                print(f"An error occurred in repeat_jackpot_rules: {e}")
                continue
            message_index = (message_index + 1) % len(messages)
            await asyncio.sleep(60)
    
    async def connect_yandex(self):
        try:
            self.client = await ClientAsync(YANDEX_TOKEN).init()
            logger.info("Успешное подключение к Яндекс.Музыке")
        except Exception as e:
            logger.error(f"Ошибка подключения к Яндекс.Музыке: {e}")

    async def on_start(self, session_metadata):
        await self.connect_yandex()
        asyncio.create_task(self.repeat_jackpot_rules())
        await self.highrise.walk_to(Position(16.5, 0.0, 20.5))

        print("Xenbot is armed and ready!")
        print("Bot is starting... cleaning up any active streams.")
        await self.stop_existing_stream()

        self.currently_playing = False
        await self.load_queue()

        await asyncio.sleep(5)

        self.play_task = asyncio.create_task(self.playback_loop())
        self.ready.set()
        await asyncio.sleep(3)

    async def on_user_join(self, user: User, position: Position) -> None:
        await self.highrise.send_whisper(user.id, self.messages_dict_dj["command_list"])
        await self.highrise.send_whisper(user.id, self.messages_dict_dj["command_list2"])
        self.add_user_to_db(user.username)

    def add_user_to_db(self, username):
        try:
            self.cursor.execute("INSERT OR IGNORE INTO users (username) VALUES (?)", (username,))
            self.conn.commit()
        except sqlite3.Error as e:
            print(f"Database error adding user: {e}")

    def get_user_balance(self, username):
        try:
            self.cursor.execute("SELECT balance FROM users WHERE username = ?", (username,))
            result = self.cursor.fetchone()
            return result[0] if result else 0
        except sqlite3.Error as e:
            print(f"Database error getting balance: {e}")
            return 0

    def update_user_balance(self, username, amount):
        try:
            self.cursor.execute("UPDATE users SET balance = balance + ? WHERE username = ?", (amount, username))
            self.conn.commit()
        except sqlite3.Error as e:
            print(f"Database error updating balance: {e}")

    async def on_tip(self, sender: User, receiver: User, tip: CurrencyItem) -> None:
        if receiver.username == "KrakenDJ":
            try:
                self.update_user_balance(sender.username, tip.amount)
                await self.highrise.chat(f"@{sender.username} пополнил(a) баланс на {tip.amount} голды!")
            except Exception as e:
                print(f"Error processing tip: {e}")

    def is_admin(self, username):
        return username in self.admins

    async def on_chat(self, user: User, message: str) -> None:
        await self.ready.wait()
        if message.lower() == "/walletdj":
            wallet = (await self.highrise.get_wallet()).content
            await self.highrise.send_whisper(user.id, f"\nУ бота в кошельке {wallet[0].amount} {wallet[0].type}")

        if message.lower().startswith("/tipmedj "):
            if user.username not in allowed_usernames:
                await self.highrise.send_whisper(user.id, "\n❌ Это команда тебе не доступна!")
                return
            parts = message.split(" ")
            if len(parts) != 2:
                await self.highrise.send_message(user.id, "Invalid command")
                return

            try:
                amount = int(parts[1])
            except:
                await self.highrise.chat("Invalid amount")
                return

            bot_wallet = await self.highrise.get_wallet()
            bot_amount = bot_wallet.content[0].amount
            if bot_amount <= amount:
                await self.highrise.chat("Not enough funds")
                return

            bars_dictionary = {10000: "gold_bar_10k", 5000: "gold_bar_5000", 1000: "gold_bar_1k",
                               500: "gold_bar_500", 100: "gold_bar_100", 50: "gold_bar_50",
                               10: "gold_bar_10", 5: "gold_bar_5", 1: "gold_bar_1"}
            fees_dictionary = {10000: 1000, 5000: 500, 1000: 100, 500: 50,
                               100: 10, 50: 5, 10: 1, 5: 1, 1: 1}

            tip = []
            total = 0
            for bar in bars_dictionary:
                if amount >= bar:
                    bar_amount = amount // bar
                    amount = amount % bar
                    for i in range(bar_amount):
                        tip.append(bars_dictionary[bar])
                        total = bar + fees_dictionary[bar]
            if total > bot_amount:
                await self.highrise.chat("Not enough funds")
                return
            for bar in tip:
                await self.highrise.tip_user(user.id, bar)

        if message.startswith('/cash'):
            if user.username not in allowed_usernames:
                await self.highrise.send_whisper(user.id, "\n❌ Это команда тебе не доступна!")
                return
            parts = message.split()
            if len(parts) > 2:
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
                await self.highrise.send_whisper(user.id, "Используй: /cash @username amount")

        if message.startswith('/play '):
            song_request = message[len('/play '):].strip()
            cost = 10
            balance = self.get_user_balance(user.username)

            if self.is_valid_yandex_url(song_request):
                await self.highrise.send_whisper(user.id, "Похоже, вы ввели ссылку. Пожалуйста, используйте команду /linkplay для воспроизведения по ссылке.")
                return

            if balance >= cost:
                self.update_user_balance(user.username, -cost)
                await self.add_to_queue(song_request, user.username, search_by_title=True)
            else:
                await self.highrise.send_whisper(user.id, f"\n❌ Недостаточно средств для запроса песни. Нужно {cost} голды.\n\nВаш баланс: {balance}.")

        if message.startswith('/linkplay '):
            song_request = message[len('/linkplay '):].strip()
            cost = 10
            balance = self.get_user_balance(user.username)

            if not self.is_valid_yandex_url(song_request):
                await self.highrise.send_whisper(user.id, "Неверная ссылка, я могу только по ссылкам с Yandex Music")
                return

            if balance >= cost:
                self.update_user_balance(user.username, -cost)
                await self.add_to_queue(song_request, user.username, search_by_title=False)
            else:
                await self.highrise.send_whisper(user.id, f"\n❌ Недостаточно средств для запроса песни. Нужно {cost} голды.\n\nВаш баланс: {balance}.")

        if message.startswith('/q'):
            page_number = 1
            try:
                page_number = int(message.split(' ')[1])
            except (IndexError, ValueError):
                pass
            await self.check_queue(page_number)
        elif message.startswith('/bal'):
            balance = self.get_user_balance(user.username)
            await self.highrise.send_whisper(user.id, f"Ваш баланс: {balance}")
        elif message.startswith('/skip'):
            await self.skip_song(user)
        elif message.startswith('/np'):
            await self.now_playing()
        elif message.startswith('/shutdown'):
            if self.is_admin(user.username):
                await self.shutdown_bot()
            else:
                await self.highrise.send_whisper(user.id, "\n❌ Это команда тебе не доступна!")

    def is_valid_yandex_url(self, url):
        try:
            parsed_url = urlparse(url)
            return 'music.yandex.ru' in parsed_url.netloc
        except:
            return False

    async def shutdown_bot(self):
        print("Shutting down the bot...")
        await self.highrise.chat("Бот перезагружается...")

        await self.stop_existing_stream()

        if self.play_task:
            self.play_task.cancel()
            try:
                await self.play_task
            except asyncio.CancelledError:
                pass

        self.close_db()
        self.clear_downloads_folder()
        print("Bot shutdown initiated.")
        raise Exception("Bot is shutting down and restarting")

    async def add_to_queue(self, song_request, owner, search_by_title=True):
        await self.request_queue.put({'song_request': song_request, 'owner': owner, 'search_by_title': search_by_title})
        asyncio.create_task(self.process_request_queue())

    async def process_request_queue(self):
        if self.request_lock.locked():
            return

        async with self.request_lock:
            while not self.request_queue.empty():
                request_data = await self.request_queue.get()
                song_request = request_data['song_request']
                owner = request_data['owner']
                search_by_title = request_data['search_by_title']

                await self.highrise.chat(f"@{owner} ищу песню... Пожалуйста, подождите.")

                try:
                    file_path, title, duration, is_playlist = await self.download_yandex_audio(song_request, search_by_title)
                except Exception as e:
                    print(f"Error while downloading: {e}")
                    await self.highrise.chat(f"Произошла ошибка при скачивании трека, попробуйте позже. @{owner}")
                    self.request_queue.task_done()
                    continue

                if file_path and title:
                    if is_playlist:
                        await asyncio.sleep(2)
                        await self.highrise.chat(f"Плейлисты не поддерживаются, я могу скачать только один трек. @{owner}")
                        if os.path.exists(file_path):
                            os.remove(file_path)
                        self.request_queue.task_done()
                        continue
                if duration > 240:
                    await self.highrise.chat(f"@{owner} трек '{title}' превышает 4 минуты и не может быть добавлен в очередь.\n\nМаксимальная длительность трека 4 минуты.")
                    if os.path.exists(file_path):
                        os.remove(file_path)
                    self.request_queue.task_done()
                    continue

                formatted_duration = self.format_time(duration)
                message = f"""
      🎶 Добавлено в очередь:
         🎵 '{title}'
        ⏱️ Длительность: {formatted_duration}
       👤 Включил: @{owner}
              """
                self.song_queue.append({'title': title, 'file_path': file_path, 'owner': owner, 'duration': duration})
                await self.highrise.chat(message)

                await self.save_queue()

                if not self.play_task or self.play_task.done():
                    print("Playback loop has been created.")
                    self.play_task = asyncio.create_task(self.playback_loop())

                self.play_event.set()
                self.request_queue.task_done()

    async def download_yandex_audio(self, query: str, search_by_title: bool = True) -> tuple:
        logger.info(f"Начало загрузки трека: {query}, поиск по названию: {search_by_title}") # Log
        try:
            if self.client is None:
                await self.connect_yandex()
                if self.client is None:
                    logger.error("Yandex Music client не инициализирован.")
                    return None, None, 0, False

            if search_by_title:
                logger.info(f"Поиск трека по названию: {query}")  # Log
                search_result = await self.client.search(query, type_='track')
                if not search_result.tracks:
                    logger.warning(f"Не найдено треков по запросу: {query}")  # Log
                    return None, None, 0, False
                track = search_result.tracks.results[0]
            else:
                logger.info(f"Поиск трека по ссылке: {query}")  # Log
                track_id = self.parse_track_id(query)
                if not track_id:
                    logger.warning(f"Неверный ID трека в запросе: {query}")  # Log
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
        logger.info(f"Начало скачивания файла: {url} -> {path}")
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(url) as resp:
                    if resp.status == 200:
                        logger.info(f"Файл {path} успешно скачан, сохраняю на диск.")
                        with open(path, 'wb') as f:
                            async for chunk in resp.content.iter_chunked(1024):
                                f.write(chunk)
                        logger.info(f"Файл {path} сохранен успешно.")
                    else:
                        logger.error(f"Ошибка при скачивании файла: {resp.status}")
            except Exception as e:
                logger.error(f"Ошибка при скачивании файла: {e}")


    def parse_track_id(self, url: str) -> int:
        try:
            path = urlparse(url).path
            return int(path.split('/')[-1])
        except:
            return None

    async def check_queue(self, page_number=1):
        try:
            songs_per_page = 2
            total_songs = len(self.song_queue)
            total_pages = (total_songs + songs_per_page - 1) // songs_per_page

            if total_songs == 0:
                await self.highrise.chat("В настоящее время очередь пуста.")
                return

            if page_number < 1 or page_number > total_pages:
                await self.highrise.chat("Неверный номер страницы.")
                return

            queue_message = f"В очереди есть {total_songs} песни (Страница {page_number}/{total_pages}):\n\n"
            start_index = (page_number - 1) * songs_per_page
            end_index = min(start_index + songs_per_page, total_songs)

            for index, song in enumerate(self.song_queue[start_index:end_index], start=start_index + 1):
                duration = song.get('duration', 0)
                duration_minutes = int(duration // 60)
                duration_seconds = int(duration % 60)
                formatted_duration = f"{duration_minutes}:{duration_seconds:02d}"

                queue_message += f"{index}. '{song['title']}' ({formatted_duration}) req by @{song['owner']}\n"

            await self.highrise.chat(queue_message)

            if page_number < total_pages:
                await self.highrise.chat(f"Используйте '/q {page_number + 1}' для просмотра следующей страницы.")

        except Exception as e:
            print(f"Произошла ошибка: {str(e)}")

    async def now_playing(self):
        if self.current_song:
            current_song_owner = self.current_song['owner']
            song_duration = self.current_song['duration']

            current_position = 0
            if hasattr(self, 'current_position_ms') and hasattr(self, 'start_time_ms') and isinstance(self.current_position_ms, (int, float)) and isinstance(self.start_time_ms, (int, float)):
                current_position = int(self.current_position_ms - self.start_time_ms) // 1000

            progress_bar = self.create_progress_bar(current_position, song_duration, 20)
            formatted_duration = self.format_time(song_duration)
            formatted_current = self.format_time(current_position)

            message = f"""
  🎧 Сейчас играет: {self.current_song['title']}
   🎵 Включил: @{current_song_owner}

      {progress_bar} {formatted_current}/{formatted_duration}
            """
            await self.highrise.chat(message)
        else:
            await self.highrise.chat("В настоящее время не играет ни одна песня.")

    async def playback_loop(self):
        while True:
            await self.play_event.wait()
            while self.song_queue:
                self.currently_playing = True
                next_song = self.song_queue.pop(0)
                self.current_song = next_song
                self.start_time_ms = None

                song_title = next_song['title']
                song_owner = next_song['owner']
                file_path = next_song['file_path']
                formatted_duration = self.format_time(next_song['duration'])
                message = f"""
     ▶️ Далее играет:
          🎵 '{song_title}'
          ⏱️ Длительность: {formatted_duration}
          👤 Включил @{song_owner}
                     """
                await self.highrise.chat(message)
                print(f"Playing: {song_title}")

                await self.stream(file_path)

                while self.ffmpeg_process and self.ffmpeg_process.returncode is None:
                    await asyncio.sleep(0.1)

                if os.path.exists(file_path):
                    os.remove(file_path)

                await self.save_queue()

            if not self.song_queue:
                self.play_event.clear()
                await self.highrise.chat("Теперь очередь пуста.")

            self.currently_playing = False

    def create_progress_bar(self, current_position, total_duration, bar_length=20):
        if total_duration == 0:
            return "[====================]"
        progress = min(max(current_position, 0), total_duration) / total_duration
        filled_length = int(round(bar_length * progress))
        empty_length = bar_length - filled_length
        bar = "█" * filled_length + "░" * empty_length
        return f"[{bar}]"

    def format_time(self, total_seconds):
        minutes = int(total_seconds // 60)
        seconds = int(total_seconds % 60)
        return f"{minutes:02d}:{seconds:02d}"

    async def stream(self, mp3_file_path):
        await asyncio.create_task(self._stream_to__thread(mp3_file_path))

    async def _stream_to__thread(self, mp3_file_path):
        try:
            icecast_server = "live.radioking.com"
            icecast_port = 80
            mount_point = "/kraken-radio"
            username = "wqeqwewq_qwewqe"
            password = "teenparalich000!"
            icecast_url = f"icecast://{username}:{password}@{icecast_server}:{icecast_port}{mount_point}"

            if self.ffmpeg_process:
                self.ffmpeg_process.terminate()
                await self.ffmpeg_process.wait()
                self.ffmpeg_process = None

            command = [
                'ffmpeg', '-re', '-i', mp3_file_path,
                '-f', 'mp3', '-acodec', 'libmp3lame', '-ab', '192k',
                '-ar', '44100', '-ac', '2',
                '-reconnect', '1', '-reconnect_streamed', '1',
                '-reconnect_delay_max', '2',
                '-progress', 'pipe:1',
                icecast_url
            ]

            self.ffmpeg_process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            self.current_position_ms = 0
            self.start_time_ms = None

            while True:
                line = await self.ffmpeg_process.stdout.readline()
                if not line:
                    if self.ffmpeg_process.returncode is not None:
                        break
                    else:
                        continue
                line = line.decode('utf-8').strip()
                if line.startswith("out_time_ms="):
                    ms = int(line.split("=")[1])
                    self.current_position_ms = ms
                    if self.start_time_ms is None:
                        self.start_time_ms = ms
                if line.startswith("progress=end"):
                    break
                if self.stream_stop_event.is_set():
                    break

            if self.ffmpeg_process.returncode != 0:
                print(f"FFmpeg process exited with code {self.ffmpeg_process.returncode}")
                if self.ffmpeg_process.stderr:
                    error = await self.ffmpeg_process.stderr.read()
                    print(f"FFmpeg error: {error.decode('utf-8')}")

        except Exception as e:
            print(f"Error streaming to Radioking: {e}")
        finally:
            if self.ffmpeg_process:
                if self.ffmpeg_process.returncode is None:
                    self.ffmpeg_process.terminate()
                    await self.ffmpeg_process.wait()
                self.ffmpeg_process = None
        self.stream_stop_event.clear()

    async def skip_song(self, user):
        if self.currently_playing:
            if self.is_admin(user.username) or (self.current_song and self.current_song['owner'] == user.username):
                self.stream_stop_event.set()
                if self.ffmpeg_process:
                    self.ffmpeg_process.terminate()
                await self.highrise.chat(f"@{user.username} пропустил песню.")
            else:
                await self.highrise.chat("Только администраторы или запросившие песню могут пропускать песни.")
        else:
            await self.highrise.chat("В настоящее время не воспроизводится ни одна песня, которую можно пропустить.")

    def clear_downloads_folder(self):
        downloads_path = 'downloads'
        if os.path.exists(downloads_path):
            for filename in os.listdir(downloads_path):
                file_path = os.path.join(downloads_path, filename)
                try:
                    if os.path.isfile(file_path) or os.path.islink(file_path):
                        os.remove(file_path)
                    elif os.path.isdir(file_path):
                        shutil.rmtree(file_path)
                except Exception as e:
                    print(f"Error deleting file {file_path}: {e}")

    async def stop_existing_stream(self):
        if self.ffmpeg_process:
            print("Stopping active stream...")
            try:
                self.stream_stop_event.set()
                self.ffmpeg_process.terminate()
                await self.ffmpeg_process.wait()
                print("Stream terminated successfully.")
            except Exception as e:
                print(f"Error while stopping stream: {e}")
            self.ffmpeg_process = None
        else:
            print("No active stream to stop.")

    def close_db(self):
      self.conn.close()

    async def save_queue(self):
        try:
            queue_json = json.dumps(self.song_queue)
            self.cursor.execute("INSERT OR REPLACE INTO queue_data (id, queue) VALUES (1, ?)", (queue_json,))
            self.conn.commit()
            print("Saved the queue successfully!")
        except sqlite3.Error as e:
            print(f"Database error saving queue: {e}")

    async def load_queue(self):
        try:
            self.cursor.execute("SELECT queue FROM queue_data WHERE id = 1")
            result = self.cursor.fetchone()
            if result:
                self.song_queue = json.loads(result[0])
                print("Loaded queue successfully!")
            else:
                print("No queue data found in the database.")
        except sqlite3.Error as e:
            print(f"Database error loading queue: {e}")
            self.song_queue = []

    async def on_close(self):
        self.close_db()

allowed_usernames = ["fedorballz", "Skara0"]
