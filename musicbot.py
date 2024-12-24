from highrise import *
from highrise.models import *
import yt_dlp as youtube_dl
import os
import subprocess
from highrise import BaseBot, User
from concurrent.futures import ThreadPoolExecutor
import asyncio
import shutil
from highrise.__main__ import BotDefinition, main, import_module, arun
import time
import sqlite3
import json # Import the json library for queue persistence

# --- DATABASE SETUP ---
db_path = "musicbot.db"

def init_db():
    conn = sqlite3.connect(db_path)
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

# BOT SETTINGS #
bot_file_name = "musicbot"
bot_class_name = "xenoichi"
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
      except Exception as e:
        import traceback
        print("Caught an exception:")
        traceback.print_exc()
        time.sleep(1)
        continue


class xenoichi(BaseBot):
    def __init__(self):
        super().__init__()
        self.conn = sqlite3.connect(db_path)
        self.cursor = self.conn.cursor()
        self.song_queue = []
        self.currently_playing = False
        self.skip_event = asyncio.Event()
        self.ffmpeg_process = None
        self.currently_playing_title = None
        self.admins = {'fedorballz', 'Skara0'}  # Add your admin usernames here
        self.ready = False
        self.play_lock = asyncio.Lock()
        self.play_task = None
        self.play_event = asyncio.Event()
        self.valid_url_prefixes = ['https://www.youtube.com/', 'https://youtube.com/', 'https://youtu.be/', "https://on.soundcloud.com/", "https://soundcloud.com"] #Added valid prefixes

    def close_db(self):
        self.conn.close()

    messages_dict_dj = {
        "greeting1": "\n🎵 Включаем музыку!  Добро пожаловать к нашему DJ-боту!\n\n🎧 Закажи песню командой /play [название песни] или /linkplay [ссылка Youtube или SoundCloud].\n",
        "greeting2": "\n🎶 Привет! \n\n🎵 Используй команду /play [название песни] или /linkplay [ссылка Youtube или SoundCloud], чтобы добавить трек в очередь.\n",
        "greeting3": "\n🎤 DJ-бот в сети!  Заказывай свои любимые песни!\n\n🎶 Отправь чаевые, чтобы пополнить баланс и заказать песню\n",
        "balance_reminder": "\n💰 Проверь свой баланс командой /bal\n\n🎧 Не забудь, что за каждую песню списывается 10 голды!\n",
        "tip_reminder": "\n✨ Хочешь поддержать бота?  Отправь чаевые в размере 10г и закажи песню\n\n🎵 /play [название песни] или /linkplay [ссылка Youtube или SoundCloud]\n",
        "command_list": "\nСписок команд:\n\n/play [название песни] - Заказать песню по названию\n/linkplay [ссылка] - Заказать песню по ссылке Youtube или SoundCloud",
        "command_list2": "\nСписок команд: /skip - пропустить свой трек\n/bal - Проверить баланс\n/np - Узнать название трека\n/q - узнать очередь\n\nОтправь чаевые, чтобы пополнить баланс"
    }


    async def repeat_jackpot_rules(self):
        messages = list(self.messages_dict_dj.values())  # Get all messages
        message_index = 0
        while True:
            message = messages[message_index]
            await self.highrise.chat(message)
            message_index = (message_index + 1) % len(messages)  # Cycle through messages
            await asyncio.sleep(60)

    async def on_start(self, session_metadata):
        asyncio.create_task(self.repeat_jackpot_rules())
        await self.highrise.walk_to(Position(16.5, 0.0, 20.5))

        print("Xenbot is armed and ready!")
        print("Bot is starting... cleaning up any active streams.")

        await self.stop_existing_stream()

        self.currently_playing = False
        await self.load_queue() # Load the queue on bot start

        await asyncio.sleep(5)

        self.play_task = asyncio.create_task(self.playback_loop())

        await asyncio.sleep(3)
        self.ready = True

    async def on_user_join(self, user: User, position: Position) -> None:
        await self.highrise.send_whisper(user.id, f"\nСписок команд:\n\n/play [название песни] - Заказать песню по названию\n/linkplay [ссылка] - Заказать песню по ссылке Youtube или SoundCloud")
        await self.highrise.send_whisper(user.id, f"\n/skip - пропустить свой трек\n/bal - Проверить баланс\n/np - Узнать название трека\n/q - узнать очередь\n\nОтправь чаевые, чтобы пополнить баланс")
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
        if receiver.username == "KrakenDJ":  # Check if the tip is for the bot
            try:
                # Reduce sender's balance
                self.update_user_balance(sender.username, tip.amount)
                await self.highrise.chat(f"@{sender.username} пополнил(a) баланс на {tip.amount} голды!")
            except Exception as e:
                print(f"Error processing tip: {e}")  # Handle potential errors

    def is_admin(self, username):
        return username in self.admins

    async def on_chat(self, user: User, message: str) -> None:
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
            #checks if the amount is valid
            try:
                amount = int(parts[1])
            except:
                await self.highrise.chat("Invalid amount")
                return
            #checks if the bot has the amount
            bot_wallet = await self.highrise.get_wallet()
            bot_amount = bot_wallet.content[0].amount
            if bot_amount <= amount:
                await self.highrise.chat("Not enough funds")
                return
            #converts the amount to a string of bars and calculates the fee
            """Possible values are: "gold_bar_1",
            "gold_bar_5", "gold_bar_10", "gold_bar_50", 
            "gold_bar_100", "gold_bar_500", 
            "gold_bar_1k", "gold_bar_5000", "gold_bar_10k" """
            bars_dictionary = {10000: "gold_bar_10k", 
                               5000: "gold_bar_5000",
                               1000: "gold_bar_1k",
                               500: "gold_bar_500",
                               100: "gold_bar_100",
                               50: "gold_bar_50",
                               10: "gold_bar_10",
                               5: "gold_bar_5",
                               1: "gold_bar_1"}
            fees_dictionary = {10000: 1000,
                               5000: 500,
                               1000: 100,
                               500: 50,
                               100: 10,
                               50: 5,
                               10: 1,
                               5: 1,
                               1: 1}
            #loop to check the highest bar that can be used and the amount of it needed
            tip = []
            total = 0
            for bar in bars_dictionary:
                if amount >= bar:
                    bar_amount = amount // bar
                    amount = amount % bar
                    for i in range(bar_amount):
                        tip.append(bars_dictionary[bar])
                        total = bar+fees_dictionary[bar]
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
        if message.startswith('/play '):
            if self.ready:
                song_request = message[len('/play '):].strip()

                if self.is_valid_url(song_request):
                    await self.highrise.send_whisper(user.id, "Похоже, вы ввели ссылку. Пожалуйста, используйте команду /linkplay для воспроизведения по ссылке.")
                    return

                cost = 10
                balance = self.get_user_balance(user.username)

                if balance >= cost:
                    self.update_user_balance(user.username, -cost)
                    await self.add_to_queue(song_request, user.username, search_by_title = True)
                else:
                    await self.highrise.send_whisper(user.id, f"\n❌Недостаточно средств для запроса песни. Нужно {cost} голды.\n\nВаш баланс: {balance}.")
            else:
                await self.highrise.chat("Бот загружается. Подождите.")
        if message.startswith('/linkplay '): # search by link
            if self.ready:
                song_request = message[len('/linkplay '):].strip()

                if not self.is_valid_url(song_request):
                    await self.highrise.send_whisper(user.id, "Неверная ссылка, я могу только по Youtube или SoundCloud")
                    return

                cost = 10
                balance = self.get_user_balance(user.username)

                if balance >= cost:
                    self.update_user_balance(user.username, -cost)
                    await self.add_to_queue(song_request, user.username, search_by_title = False)
                else:
                    await self.highrise.send_whisper(user.id, f"\n❌Недостаточно средств для запроса песни. Нужно {cost} голды.\n\nВаш баланс: {balance}.")
            else:
                await self.highrise.chat("Бот загружается. Подождите.")
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

    def is_valid_url(self, url):
        for prefix in self.valid_url_prefixes:
            if url.startswith(prefix):
                return True
        return False

    async def add_to_queue(self, song_request, owner, search_by_title = True):
        await self.highrise.chat(f"Ищу песню... Пожалуйста, подождите.")
        try:
            file_path, title, duration, is_playlist = await self.download_youtube_audio(song_request, search_by_title)
        except Exception as e:
            print(f"Error while downloading: {e}")
            await self.highrise.chat(f"Произошла ошибка при скачивании трека, попробуйте позже.")
            return

        if file_path and title:
           if is_playlist:
              await asyncio.sleep(2)
              await self.highrise.chat(f"Плейлисты не поддерживаются, я могу скачать только один трек. @{owner}")
              if os.path.exists(file_path):
                  os.remove(file_path)
              return
           if duration > 240:
                await self.highrise.chat(f"@{owner} трек '{title}' превышает 4 минуты и не может быть добавлен в очередь.\n\nМаксимальная длительность трека 4 минуты.")
                if os.path.exists(file_path):
                    os.remove(file_path)
                return
           self.song_queue.append({'title': title, 'file_path': file_path, 'owner': owner, 'duration': duration})
           await self.highrise.chat(f"Добавлено в очередь: '{title}' \n\nВключил: @{owner}")
           
           await self.save_queue()

           if not self.play_task or self.play_task.done():
               print("Playback loop has been created.")
               self.play_task = asyncio.create_task(self.playback_loop())

           self.play_event.set()

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
                # Get the duration, default to 0 if not available
                duration = song.get('duration', 0)

                # Format the duration as MM:SS
                duration_minutes = int(duration // 60)
                duration_seconds = int(duration % 60)
                formatted_duration = f"{duration_minutes}:{duration_seconds:02d}"

                queue_message += f"{index}. '{song['title']}' ({formatted_duration}) req by @{song['owner']}\n"

            await self.highrise.chat(queue_message)

            if page_number < total_pages:
                await self.highrise.chat(f"Используйте '/q {page_number + 1}' для просмотра следующей страницы.")

        except Exception as e:
            # Handle any error that occurs
            print(f"Произошла ошибка: {str(e)}")

    async def download_youtube_audio(self, song_request, search_by_title = True):
        """Downloads audio from YouTube, trying link first, then search, and returns the file path and title."""
        try:
            ydl_opts = {
                'format': 'bestaudio/best',
                'outtmpl': 'downloads/%(id)s.%(ext)s',
                'default_search': 'ytsearch' if search_by_title else None,  # Условный поиск
                'quiet': True,
                'noplaylist': True,
            }

            with youtube_dl.YoutubeDL(ydl_opts) as ydl:
                if search_by_title: #Searching by title
                    info = ydl.extract_info(f"ytsearch:{song_request}", download=False)
                    if 'entries' in info:
                        info = info['entries'][0]

                    video_id = info['id']
                    title = info['title']
                    file_extension = info['ext']
                    file_path = f"downloads/{video_id}.{file_extension}"
                    
                else: #Searching by link
                    info = ydl.extract_info(song_request, download = False)

                    if 'entries' in info:
                        if len(info['entries']) > 1:
                           return None, None, 0, True
                        info = info['entries'][0]

                    video_id = info['id']
                    title = info['title']
                    file_extension = info['ext']
                    file_path = f"downloads/{video_id}.{file_extension}"

                if os.path.exists(file_path):
                    print(f"The file '{file_path}' already exists, skipping download.")
                    return file_path, title, info['duration'], False
                
                info = ydl.extract_info(song_request, download=True)
                if 'entries' in info:
                    if len(info['entries']) > 1:
                        return None, None, 0, True
                    info = info['entries'][0]

                video_id = info['id']
                file_extension = info['ext']
                file_path = f"downloads/{video_id}.{file_extension}"
                print(f"Downloaded: {file_path} with title: {title}")
                return file_path, title, info['duration'], False
        except Exception as e:
              print(f"Error downloading the song: {e}")
              return None, None, 0, False


    async def now_playing(self):
        if self.currently_playing_title:
            current_song_owner = self.current_song['owner'] if self.current_song else "Unknown"
            asyncio.sleep(2)
            await self.highrise.chat(f"Сейчас играет: '{self.currently_playing_title}'\n\nВключил @{current_song_owner}")
        else:
            await self.highrise.chat("В настоящее время не играет ни одна песня.")


    async def playback_loop(self):
        while True:
            await self.play_event.wait()
            while self.song_queue:
                self.currently_playing = True
                next_song = self.song_queue.pop(0)
                self.current_song = next_song
                self.currently_playing_title = next_song['title']

                song_title = next_song['title']
                song_owner = next_song['owner']
                file_path = next_song['file_path']

                await self.highrise.chat(f"Далее: '{song_title}'\n\nВключил @{song_owner}")
                print(f"Playing: {song_title}")

                mp3_file_path = await self.convert_to_mp3(file_path)

                if mp3_file_path:
                    await self.stream(mp3_file_path)

                    if os.path.exists(mp3_file_path):
                        os.remove(mp3_file_path)
                    if os.path.exists(file_path):
                        os.remove(file_path)

                await self.save_queue() #Save after each song
        
            if not self.song_queue:
                self.play_event.clear()
                await self.highrise.chat("Теперь очередь пуста.")

            self.currently_playing = False
            self.currently_playing_title = None

    async def convert_to_mp3(self, audio_file_path):
        try:
            if audio_file_path.endswith('.mp3'):
                return audio_file_path

            mp3_file_path = audio_file_path.replace(os.path.splitext(audio_file_path)[1], '.mp3')

            if os.path.exists(mp3_file_path):
                print(f"MP3 file {mp3_file_path} already exists. Skipping conversion.")
                return mp3_file_path
            
            subprocess.run([
                'ffmpeg', '-i', audio_file_path,
                '-acodec', 'libmp3lame', '-ab', '192k', '-ar', '44100', '-ac', '2', mp3_file_path
            ], check=True)

            return mp3_file_path
        except Exception as e:
            print(f"Error converting to MP3: {e}")
            return None

    async def stream(self, mp3_file_path):
        with ThreadPoolExecutor() as executor:
            future = executor.submit(self._stream_to__thread, mp3_file_path)
            await asyncio.get_event_loop().run_in_executor(None, future.result)

    def _stream_to__thread(self, mp3_file_path):
        try:
            icecast_server = "live.radioking.com"
            icecast_port = 80
            mount_point = "/dfsadfasfdsa"
            username = "Kraken_Kraken"
            password = "teenparalich000!"
            icecast_url = f"icecast://{username}:{password}@{icecast_server}:{icecast_port}{mount_point}"

            if self.ffmpeg_process:
                self.ffmpeg_process.terminate()
                self.ffmpeg_process.wait()
                self.ffmpeg_process = None

            command = [
                'ffmpeg', '-re', '-i', mp3_file_path,
                '-f', 'mp3', '-acodec', 'libmp3lame', '-ab', '192k',
                '-ar', '44100', '-ac', '2', '-reconnect', '1', '-reconnect_streamed', '1',
                '-reconnect_delay_max', '2', icecast_url
            ]

            self.ffmpeg_process = subprocess.Popen(command)
            self.ffmpeg_process.wait()
        except Exception as e:
            print(f"Error streaming to Radioking: {e}")

    async def skip_song(self, user):
        """Allows an admin or the requester of the current song to skip."""
        if self.currently_playing:
           if self.is_admin(user.username) or (self.current_song and self.current_song['owner'] == user.username):
               self.skip_event.set()
               if self.ffmpeg_process:
                 self.ffmpeg_process.terminate()
               await self.highrise.chat(f"@{user.username} пропустил песню.")
           else:
                await self.highrise.chat("Только администраторы могут пропускать песни.")
        else:
            await self.highrise.chat("В настоящее время не воспроизводится ни одна песня, которую можно пропустить.")


    def clear_downloads_folder(self):
        """Deletes all files in the downloads folder."""
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
        """Check if an active stream is running and stop it if necessary."""
        if self.ffmpeg_process:
            print("Stopping active stream...")
            try:
                self.ffmpeg_process.terminate()
                await asyncio.sleep(1)
                if self.ffmpeg_process.poll() is None:
                    self.ffmpeg_process.kill()
                print("Stream terminated successfully.")
            except Exception as e:
                print(f"Error while stopping stream: {e}")
            self.ffmpeg_process = None
        else:
            print("No active stream to stop.")

    async def save_queue(self):
        """Saves the current song queue to the database."""
        try:
            queue_json = json.dumps(self.song_queue)
            self.cursor.execute("INSERT OR REPLACE INTO queue_data (id, queue) VALUES (1, ?)", (queue_json,))
            self.conn.commit()
            print("Saved the queue successfully!")
        except sqlite3.Error as e:
            print(f"Database error saving queue: {e}")

    async def load_queue(self):
        """Loads the song queue from the database."""
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
            self.song_queue = [] # In case loading fails, empty queue to not crash

    async def on_close(self):
        self.close_db()

allowed_usernames = ["fedorballz", "Skara0"]
