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
        self.admins = {'fedorballz', 'Skara0'} # Add your admin usernames here
        self.ready = False
        self.play_lock = asyncio.Lock()
        self.play_task = None
        self.play_event = asyncio.Event()

    def close_db(self):
        self.conn.close()

    async def on_start(self, session_metadata):
        await self.highrise.walk_to(Position(16.5, 0.0, 20.5))

        print("Xenbot is armed and ready!")
        print("Bot is starting... cleaning up any active streams.")
        
        await self.stop_existing_stream()

        self.currently_playing = False

        await asyncio.sleep(5)

        self.play_task = asyncio.create_task(self.playback_loop())

        await asyncio.sleep(3)
        self.ready = True

    async def on_user_join(self, user: User, position: Position) -> None:
        await self.highrise.send_whisper(user.id, "Welcome! I'm the DJ BOT")
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
                await self.highrise.chat(f"Thank you @{sender.username} for the tip of {tip.amount} units!")
            except Exception as e:
                await self.highrise.chat(f"Error processing tip: {e}") # Handle potential errors

    def is_admin(self, username):
        return username in self.admins

    async def on_chat(self, user: User, message: str) -> None:
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
                        await self.highrise.send_whisper(user.id, f"Выдал {amount} ему @{target_username}'s на баланс")
                    else:
                        await self.highrise.send_whisper(user.id, "Amount must be positive.")
                except ValueError:
                    await self.highrise.send_whisper(user.id, "Invalid amount. Please enter a number.")
                except Exception as e:
                    await self.highrise.send_whisper(user.id, f"Error adding to @{target_username}'s balance: {e}")
            else:
                await self.highrise.send_whisper(user.id, "Usage: /cash @username amount") #Correct usage
        if message.startswith('/play '):
            if self.ready:
                song_request = message[len('/play '):].strip()
                cost = 10
                balance = self.get_user_balance(user.username)
                if balance >= cost:
                    self.update_user_balance(user.username, -cost)
                    await self.add_to_queue(song_request, user.username)
                else:
                    await self.highrise.send_whisper(user.id, f"Insufficient balance. The song costs {cost} units. Your current balance is {balance}.")
            else:
                await self.highrise.chat("Bot is loading. Please wait.")
        elif message.startswith('/balance'):
            balance = self.get_user_balance(user.username)
            await self.highrise.send_whisper(user.id, f"Your balance: {balance}")
        elif message.startswith('/skip'):
            await self.skip_song(user)
        elif message.startswith('/np'):
            await self.now_playing()

    async def add_to_queue(self, song_request, owner):

        await self.highrise.chat(f"Searching song request...")
        file_path, title = await self.download_youtube_audio(song_request)
        if file_path and title:
            self.song_queue.append({'title': title, 'file_path': file_path, 'owner': owner})
            await self.highrise.chat(f"Added to queue: '{title}' \n\nRequested by @{owner}")
            
            if not self.play_task or self.play_task.done():
                print("Playback loop has been created.")
                self.play_task = asyncio.create_task(self.playback_loop())

            self.play_event.set()

    async def download_youtube_audio(self, song_request):
        """Downloads audio from YouTube and returns the file path and title, skipping if the file is already downloaded."""
        try:

            ydl_opts = {
                'format': 'bestaudio/best',
                'outtmpl': 'downloads/%(id)s.%(ext)s',  
                'default_search': 'ytsearch',
                'quiet': True,
                'noplaylist': True,
            }

            with youtube_dl.YoutubeDL(ydl_opts) as ydl:
 
                info = ydl.extract_info(song_request, download=False)

        
                if 'entries' in info:
                    info = info['entries'][0]

                video_id = info['id']
                title = info['title']
                file_extension = info['ext']
                file_path = f"downloads/{video_id}.{file_extension}"

               
                if os.path.exists(file_path):
                    print(f"The file '{file_path}' already exists, skipping download.")
                    return file_path, title

              
                info = ydl.extract_info(song_request, download=True)
                if 'entries' in info:
                    info = info['entries'][0]

                video_id = info['id']
                file_extension = info['ext']
                file_path = f"downloads/{video_id}.{file_extension}"

                print(f"Downloaded: {file_path} with title: {title}")
                return file_path, title

        except Exception as e:
            print(f"Error downloading the song: {e}")
            return None, None


    async def now_playing(self):
        if self.currently_playing_title:
            current_song_owner = self.current_song['owner'] if self.current_song else "Unknown"
            await self.highrise.chat(f"Now playing: '{self.currently_playing_title}'\n\nRequested by @{current_song_owner}")
        else:
            await self.highrise.chat("No song is currently playing.")

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

                await self.highrise.chat(f"Up Next: '{song_title}'\n\nRequested by @{song_owner}")
                print(f"Playing: {song_title}")

                mp3_file_path = await self.convert_to_mp3(file_path)

                if mp3_file_path:
                    await self.stream(mp3_file_path)

         
                    if os.path.exists(mp3_file_path):
                        os.remove(mp3_file_path)
                    if os.path.exists(file_path):
                        os.remove(file_path)

        
            if not self.song_queue:
                self.play_event.clear()  
                await self.highrise.chat("The queue is now empty.")

         
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
                
                await self.highrise.chat(f"@{user.username} skipped the song.")
  
            else:
                await self.highrise.chat("Only the requester of the song or an admin can skip it.")
        else:
            await self.highrise.chat("No song is currently playing to skip.")

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

    async def on_close(self):
        self.close_db()


allowed_usernames = ["fedorballz", "Skara0"]