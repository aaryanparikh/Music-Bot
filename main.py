import discord
from discord.ext import commands
import yt_dlp
import asyncio
import os
import re
import time
from keep_alive import keep_alive  # 👈 prevents Replit from sleeping

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

players = {}  # guild_id -> MusicPlayer instance


async def user_not_connect(ctx):
    embed = discord.Embed(
        title="Connect to Voice Channel",
        description="⚠️ You must be in a voice channel to use this command.",
        color=discord.Color.red(),
    )
    await ctx.send(embed=embed)
    return


def user_in_voice(ctx):
    return ctx.author.voice and ctx.author.voice.channel


def get_player(ctx):
    if ctx.guild.id not in players:
        players[ctx.guild.id] = MusicPlayer()
    return players[ctx.guild.id]


async def ytdl_extract(ydl, query, download=False):
    loop = asyncio.get_event_loop()
    func = lambda: ydl.extract_info(query, download=download)
    return await loop.run_in_executor(None, func)


class MusicPlayer:
    def __init__(self):
        self.queue = []
        self.current = None
        self.loop_song = False
        self.loop_queue = False
        self.stay_connected = False
        self.autoplay = False
        self.start_time = None
        self.duration = 0
        self.previous = None
        self.history = []

    def add_to_history(self, song):
        self.history.append(song)
        if len(self.history) > 10:
            self.history.pop(0)

    async def extract_song_name(self, query):
        if any(site in query for site in ["spotify.com", "saavn.com", "jiosaavn.com", "music.apple.com", "music.amazon."]):
            try:
                ydl_opts = {"quiet": True, "extract_flat": True}
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = await ytdl_extract(ydl, query, download=False)
                    title = info.get("title")
                    uploader = info.get("uploader") or ""
                    if title:
                        return f"{title} {uploader}"
            except Exception as e:
                print("⚠️ Metadata extraction failed:", e)

            match = re.search(r"track/([a-zA-Z0-9]+)", query)
            if match:
                return f"Track {match.group(1)}"

        return query

    async def add_to_queue(self, query):
        search_query = await self.extract_song_name(query)

        is_external_link = any(site in query for site in ["spotify.com", "saavn.com", "jiosaavn.com", "music.apple.com", "music.amazon."])
        if is_external_link:
            search_query = f"ytsearch5:{search_query}"

        ydl_opts_search = {
            "format": "bestaudio/best",
            "quiet": True,
            "extract_flat": True,
            "noplaylist": True,
            "default_search": "ytsearch",
        }

        chosen_info = None
        with yt_dlp.YoutubeDL(ydl_opts_search) as ydl:
            try:
                info = await ytdl_extract(ydl, search_query, download=False)
            except Exception:
                info = None

            if info and "entries" in info:
                entries = info["entries"]
                good = [e for e in entries if e.get("duration") and e.get("duration") > 30]
                if not good:
                    good = [e for e in entries if e.get("duration", 0) > 0]
                if not good:
                    good = entries
                chosen_info = good[0]
            else:
                try:
                    with yt_dlp.YoutubeDL({"quiet": True}) as ydl_direct:
                        info = await ytdl_extract(ydl_direct, search_query, download=False)
                        chosen_info = info
                except Exception as e:
                    print("⚠️ Could not fetch info:", e)

        if not chosen_info:
            raise RuntimeError("⚠️ Could not find any suitable video for the query.")

        video_id = chosen_info.get("id")
        video_url = chosen_info.get("webpage_url") or f"https://www.youtube.com/watch?v={video_id}"

        # ✅ Safe download options
        ydl_opts_dl = {
            "format": "bestaudio/best/bestvideo+bestaudio/best",
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }],
            "outtmpl": os.path.join(os.getcwd(), "song_%(id)s.%(ext)s"),
            "quiet": True,
            "noplaylist": True,
            "ignoreerrors": True,
        }

        with yt_dlp.YoutubeDL(ydl_opts_dl) as ydl:
            info = await ytdl_extract(ydl, video_url, download=True)

        filepath = os.path.join(os.getcwd(), f"song_{info.get('id')}.mp3")
        if not os.path.exists(filepath):
            raise RuntimeError("⚠️ Could not download audio for this video.")

        song = {
            "title": info.get("title", "Unknown Title"),
            "filepath": filepath,
            "id": info.get("id"),
            "duration": info.get("duration", 0),
            "url": info.get("webpage_url"),
        }
        self.queue.append(song)
        return song

    async def play_next(self, ctx):
        prev = self.current
        will_reuse_prev = False
        if prev:
            if self.loop_song:
                will_reuse_prev = True
            elif self.loop_queue:
                will_reuse_prev = True

        if len(self.queue) == 0:
            self.current = None
            if not self.stay_connected:
                await asyncio.sleep(180)
                if ctx.voice_client and not ctx.voice_client.is_playing():
                    await ctx.voice_client.disconnect()
            return

        if prev and self.loop_song:
            self.queue.insert(0, prev)
        elif prev and self.loop_queue:
            self.queue.append(prev)

        if prev and not will_reuse_prev and os.path.exists(prev.get("filepath", "")):
            try:
                os.remove(prev["filepath"])
            except Exception as ex:
                print("⚠️ Could not delete prev file:", ex)

        self.current = self.queue.pop(0)
        self.start_time = time.time()
        self.duration = self.current.get("duration", 0)

        source = discord.PCMVolumeTransformer(
            discord.FFmpegPCMAudio(self.current["filepath"])
        )
        ctx.voice_client.play(
            source,
            after=lambda e: asyncio.run_coroutine_threadsafe(self.play_next(ctx), bot.loop),
        )

        embed = discord.Embed(
            title="▶️ Now Playing",
            description=f"**[{self.current['title']}]({self.current['url']})**",
            color=discord.Color.green(),
        )
        await ctx.send(embed=embed)


@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")
    activity = discord.Activity(type=discord.ActivityType.listening, name="🎶 Vibing on Music 🎶")
    await bot.change_presence(status=discord.Status.online, activity=activity)


@bot.command(aliases=["j"])
async def join(ctx):

    if ctx.author.voice:
        channel = ctx.author.voice.channel
        if ctx.voice_client:
            await ctx.voice_client.move_to(channel)
        else:
            await channel.connect()
        await ctx.send(f"✅ Joined {channel.name}")
    else:
        user_not_connect()
    

@bot.command(aliases=["l", "disconnect", "dc"])
async def leave(ctx):
    if not user_in_voice(ctx):
        user_not_connect
    
    if ctx.voice_client:
        await ctx.voice_client.disconnect()
        player = get_player(ctx)
        player.queue.clear()
        player.current = None
        await ctx.send("👋 Left the channel.")
    else:
        await ctx.send("⚠️ Not in a voice channel.")


@bot.command(aliases=["p"])
async def play(ctx, *, query):
    if not user_in_voice(ctx):
        await ctx.send("⚠️ You must be in a voice channel to use this command.")
        return
    
    if not ctx.voice_client:
        await ctx.invoke(join)

    player = get_player(ctx)

    try:
        if not re.match(r'https?://', query):
            query = f"ytsearch:{query}"

        song = await player.add_to_queue(query)
        await ctx.send(f"🎶 Added to queue: **{song['title']}**")
    except Exception as e:
        await ctx.send(f"❌ Could not add song: {e}")
        return

    if not ctx.voice_client.is_playing():
        await player.play_next(ctx)


@bot.command(aliases=["s"])
async def skip(ctx):
    if not user_in_voice(ctx):
        user_not_connect
    
    player = get_player(ctx)
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.stop()
        await ctx.send("⏭ Skipped.")
    else:
        await ctx.send("⚠️ Nothing is playing.")


@bot.command()
async def pause(ctx):
    if not user_in_voice(ctx):
        user_not_connect
    
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.pause()
        await ctx.send("⏸ Paused.")
    else:
        await ctx.send("⚠️ Nothing is playing.")


@bot.command()
async def resume(ctx):
    if not user_in_voice(ctx):
        user_not_connect
    
    if ctx.voice_client and ctx.voice_client.is_paused():
        ctx.voice_client.resume()
        await ctx.send("▶️ Resumed.")
    else:
        await ctx.send("⚠️ Nothing is paused.")


@bot.command()
async def loop(ctx):
    if not user_in_voice(ctx):
        user_not_connect
    
    player = get_player(ctx)
    player.loop_song = not player.loop_song
    player.loop_queue = False
    await ctx.send(f"🔁 Loop song: **{player.loop_song}**")


@bot.command()
async def repeat(ctx):
    if not user_in_voice(ctx):
        user_not_connect
    
    player = get_player(ctx)
    player.loop_queue = not player.loop_queue
    player.loop_song = False
    await ctx.send(f"🔂 Repeat queue: **{player.loop_queue}**")

@bot.command()
async def previous(ctx):
    if not user_in_voice(ctx):
        user_not_connect

    player = get_player(ctx)

    if not player.previous:
        await ctx.send("⚠️ No previous song available.")
        return

    # Put the previous song back at the front of the queue
    player.queue.insert(0, player.previous)

    # If something is currently playing, stop it to trigger the next play
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.stop()
    else:
        await player.play_next(ctx) 

    await ctx.send(f"⏮ Now playing previous song: **{player.previous['title']}**")


@bot.command(aliases=["q"])
async def queue(ctx):
    if not user_in_voice(ctx):
        user_not_connect
    
    player = get_player(ctx)
    if not player.queue:
        await ctx.send("📭 Queue is empty.")
    else:
        q = "\n".join([f"{i+1}. {s['title']}" for i, s in enumerate(player.queue)])
        await ctx.send(f"📜 **Current Queue:**\n{q}")


@bot.command(aliases=["np"])
async def nowplaying(ctx):
    if not user_in_voice(ctx):
        user_not_connect
    
    player = get_player(ctx)
    if player.current:
        elapsed = int(time.time() - (player.start_time or time.time()))
        duration = player.duration or 0

        def fmt(t):
            m, s = divmod(max(0, int(t)), 60)
            return f"{m:02d}:{s:02d}"

        bar_length = 12
        progress = 0
        progress = min(bar_length, int((elapsed / duration) * bar_length)),
        bar = "█" * progress + "░" * (bar_length - progress),

        if duration > 0:
            embed = discord.Embed(
                title="🎧 Now Playing",
                description=f"Now playing: **{player.current['title']}**\n" f"⏱ {fmt(elapsed)} / {fmt(duration)}  {bar}",
                color=discord.Color.green()
            )
            await ctx.send(embed=embed)

    else:
        embed = discord.Embed (
            title="⚠️ Nothing is playing.",
            description=f"Add songs to queue to play some music/song!",
            color=discord.Color.red()
        )
        await ctx.send(embed=embed)
        return

@bot.command(aliases=["247"])
async def stay(ctx):
    if not user_in_voice(ctx):
        user_not_connect
    
    player = get_player(ctx)
    player.stay_connected = not player.stay_connected
    
    embed = discord.Embed (
        title="24/7",
        description=f"📡 24/7 mode: **{player.stay_connected}**",
        color=discord.Color.green()
    )
    await ctx.send(embed=embed)

@bot.command()
async def autoplay(ctx):
    if not user_in_voice(ctx):
        user_not_connect
    
    player = get_player(ctx)
    player.autoplay = not player.autoplay
    embed = discord.Embed(
        title="Autoplay",
        description=f"🎵 Autoplay mode: **{player.autoplay}**",
        color=discord.Color.green()
    )


@bot.command(aliases=["v"])
async def volume(ctx, vol: int = None):
    if not user_in_voice(ctx):
        user_not_connect

    if not ctx.voice_client or not ctx.voice_client.source:
        embed = discord.Embed (
            title="⚠️ Nothing is playing.",
            description=f"Add songs to queue to play some music/song!",
            color=discord.Color.red()
        )
        await ctx.send(embed=embed)
        return

    current_volume = getattr(ctx.voice_client.source, "volume", 1.0) * 100

    if vol is None:
        embed = discord.Embed(
            title="🔊 Current Volume",
            description=f"The current volume is set to **{int(current_volume)}%**.",
            color=discord.Color.green()
        )
        embed.set_footer(text="Use `!volume <0-100>` to set volume.")
        await ctx.send(embed=embed)
    else:
        vol = max(0, min(vol, 100))  # limit volume between 0 and 100
        ctx.voice_client.source.volume = vol / 100

        embed = discord.Embed(
            title="🔊 Volume Updated",
            description=f"Volume has been set to **{vol}%**.",
            color=discord.Color.blue()
        )
        embed.set_footer(text="Enjoy your music!")
        await ctx.send(embed=embed)


@bot.command()
async def status(ctx):
    player = get_player(ctx)
    status = (
        f"🎵 Now playing: {player.current['title'] if player.current else 'Nothing'}\n"
        f"🔁 Loop song: {player.loop_song}\n"
        f"🔂 Repeat queue: {player.loop_queue}\n"
        f"📡 24/7 mode: {player.stay_connected}\n"
        f"🎶 Autoplay: {player.autoplay}\n"
        f"📋 Queue length: {len(player.queue)}"
    )
    await ctx.send(status)

# -----------------------
# HELP COMMAND
# -----------------------
    @bot.command()
    async def help(ctx):
        commands_list = """
            🎶 **Music Bot Commands**

            ▶️ **Play Songs**
            - `!play <name/link>` or `!p` → play a song
            - Supports YouTube and many other sources; paste Spotify/JioSaavn/Apple/Amazon links too.

            ⏯ **Controls**
            - `!pause` → pause
            - `!resume` → resume
            - `!skip` or `!s` → skip song
            - `!nowplaying` or `!np` → show current song + time/progress

            🔁 **Looping**
            - `!loop` → loop current song
            - `!repeat` → repeat full queue

            📜 **Queue**
            - `!queue` → show queue

            🔧 **Settings**
            - `!autoplay` → toggle related songs autoplay
            - `!stay` → toggle 24/7 mode (prevents auto-disconnect)

            🎤 **Voice**
            - `!join` or `!j` → join VC
            - `!leave` or `!l` → leave VC
            """
        await ctx.send(commands_list)

# -----------------------
# RUN BOT
# -----------------------
keep_alive()  # 👈 keeps bot running on Replit
TOKEN = os.getenv("TOKEN")  # 👈 use Replit secret
bot.run(TOKEN)
