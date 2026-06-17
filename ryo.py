import os
import discord
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv
import yt_dlp
from collections import deque
import asyncio
import random
from concurrent.futures import ProcessPoolExecutor
from keep_alive import keep_alive

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
process_pool = ProcessPoolExecutor()

FFMPEG_PATH = "ffmpeg"

MAIN_COLOR = discord.Color(0x004ee8)
ERROR_COLOR = discord.Color.red()
GUILD_STATES = {}

class GuildState:
    def __init__(self, bot_loop):
        self.queue = deque()
        self.current_song = None
        self.voice_client = None
        self.text_channel = None
        self.loop = bot_loop
        self.inactivity_task = None
        self.is_playing = False
        self.loop_enabled = False

    async def play_next_song(self):
        if self.is_playing: return

        if self.inactivity_task:
            self.inactivity_task.cancel()
            self.inactivity_task = None

        if not self.queue:
            if self.loop_enabled and self.current_song:
                self.queue.append(self.current_song)
            else:
                self.current_song = None
                self.is_playing = False
                self.inactivity_task = self.loop.create_task(self.disconnect_after_inactivity())
                return

        self.is_playing = True
        song_data = self.queue.popleft()
        if self.loop_enabled:
            self.queue.append(song_data)
        
        audio_url = song_data['audio_url']
        self.current_song = song_data

        embed = discord.Embed(title="Tocando agora", color=MAIN_COLOR)
        embed.description = f"**[{song_data['title']}]({song_data['webpage_url']})**\n`Duração: {format_duration(song_data['duration'])}`\n\nRequisitado por: {song_data['requester'].mention}"
        
        try:
            if self.text_channel: await self.text_channel.send(embed=embed)
        except discord.HTTPException: pass

        try:
            ffmpeg_options = {
                'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -nostdin',
                'options': '-vn -loglevel fatal -flags low_delay'
            }
            source = discord.FFmpegOpusAudio(audio_url, **ffmpeg_options, executable=FFMPEG_PATH)
            self.voice_client.play(source, after=self.after_play_callback)
        except Exception as e:
            print(f"Erro ao tocar a música '{song_data['title']}': {e}")
            if self.text_channel: await self.text_channel.send(f"Desculpe, não consegui tocar **{song_data['title']}**. Pulando.")
            self.after_play_callback(None)

    def after_play_callback(self, error):
        if error: print(f"Erro durante a reprodução: {error}")
        self.is_playing = False
        asyncio.run_coroutine_threadsafe(self.play_next_song(), self.loop)

    async def disconnect_after_inactivity(self):
        await asyncio.sleep(120)
        if self.voice_client and self.voice_client.is_connected() and not self.is_playing and not self.queue:
            disconnect_options = [
                "Meu tempo é precioso e não rende juros. Fui.",
                "Ficar parada aqui não me rende dinheiro. Estou indo embora."
            ]
            await self.text_channel.send(random.choice(disconnect_options))
            await self.voice_client.disconnect()
            if self.voice_client.guild.id in GUILD_STATES: del GUILD_STATES[self.voice_client.guild.id]

def format_duration(seconds):
    if seconds is None: return "Desconhecida"
    minutes, seconds = divmod(int(seconds), 60)
    return f"{minutes:02d}:{seconds:02d}"

def get_guild_state(guild_id, bot_loop) -> GuildState:
    if guild_id not in GUILD_STATES: GUILD_STATES[guild_id] = GuildState(bot_loop)
    return GUILD_STATES[guild_id]

def ytdlp_extract_sync(query, ydl_opts):
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        return ydl.extract_info(query, download=False)

async def search_ytdlp(query, is_playlist=False, playlist_limit=0):
    ydl_opts = {
        'format': 'bestaudio/best', 'noplaylist': not is_playlist,
        'extract_flat': 'in_playlist' if is_playlist else False,
        'playlistend': playlist_limit if is_playlist and playlist_limit > 0 else None,
        'nocheckcertificate': True, 'ignoreerrors': True, 'logtostderr': False,
        'quiet': True, 'no_warnings': True, 'default_search': 'auto', 'source_address': '0.0.0.0'
    }
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(process_pool, ytdlp_extract_sync, query, ydl_opts)

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="r!", intents=intents, help_command=None)

@bot.event
async def on_ready():
    activity = discord.CustomActivity(name="r!ajuda")
    await bot.change_presence(activity=activity)
    await bot.tree.sync()
    print(f"{bot.user.name}#{bot.user.discriminator} está online!")

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        error_quotes = [
            "Tocar baixo eu sei, agora esse comando aí… nunca ouvi falar.",
            "Se eu ganhasse por cada comando errado, já tinha dinheiro pra comida da semana.",
            "Isso aí não é um comando, é improviso malfeito.",
            "Vou fingir que não vi essa, tenta outra vez.",
            "Hmm… dá pra repetir isso de um jeito que faça sentido?"
        ]
        await ctx.send(random.choice(error_quotes))
    else:
        print(f"Ocorreu um erro de comando não tratado: {error}")

async def tocar_logic(ctx_or_interaction, song_query: str):
    guild = ctx_or_interaction.guild
    user = ctx_or_interaction.user if isinstance(ctx_or_interaction, discord.Interaction) else ctx_or_interaction.author
    channel = ctx_or_interaction.channel
    state = get_guild_state(guild.id, bot.loop)
    state.text_channel = channel

    voice_channel = getattr(user.voice, "channel", None)
    if not voice_channel: return "Eu não toco de graça, muito menos pro vácuo. Entre em um canal de voz."

    if state.voice_client is None or not state.voice_client.is_connected():
        state.voice_client = await voice_channel.connect(self_deaf=True)
        await asyncio.sleep(0.5)
    elif voice_channel != state.voice_client.channel:
        await state.voice_client.move_to(voice_channel)
    
    initial_message = None
    search_quotes = [
        "Hm… deixa eu ver se isso vale meu tempo.", "Procurando… mas olha, eu não faço isso de graça.",
        "Ok, tô buscando sua música. É melhor ter um baixo decente.", "Segura aí… meu sensor de bom gosto tá analisando."
    ]
    if isinstance(ctx_or_interaction, discord.Interaction):
        await ctx_or_interaction.response.defer()
        initial_message = await ctx_or_interaction.followup.send(random.choice(search_quotes))
    else:
        initial_message = await channel.send(random.choice(search_quotes))

    results = await search_ytdlp(song_query)

    if not results:
        return await initial_message.edit(content="Procurei e não achei. Conclusão: se eu não conheço, não é rock. E se não é rock, não importa.")

    video_info = results.get('entries', [results])[0]
    
    song_data = {
        'audio_url': video_info.get('url'), 'webpage_url': video_info.get('webpage_url'),
        'title': video_info.get('title', 'Sem título'), 'duration': video_info.get('duration'),
        'requester': user
    }
    
    if not song_data['audio_url']:
        return await initial_message.edit(content="Tentei, mas essa música está com a linha de baixo quebrada. Não consigo tocar.")

    state.queue.append(song_data)

    if state.is_playing:
        embed = discord.Embed(title="Adicionado à Fila", color=MAIN_COLOR)
        embed.description = f"**[{song_data['title']}]({song_data['webpage_url']})**\n`Duração: {format_duration(song_data['duration'])}`\n\nRequisitado por: {user.mention}"
        if isinstance(ctx_or_interaction, discord.Interaction):
            await ctx_or_interaction.followup.send(embed=embed)
        else:
            await channel.send(embed=embed)
    else:
        await state.play_next_song()

async def playlist_logic(ctx_or_interaction, playlist_url: str, limit: int = 0):
    guild = ctx_or_interaction.guild
    user = ctx_or_interaction.user if isinstance(ctx_or_interaction, discord.Interaction) else ctx_or_interaction.author
    channel = ctx_or_interaction.channel
    state = get_guild_state(guild.id, bot.loop)
    state.text_channel = channel

    voice_channel = getattr(user.voice, "channel", None)
    if not voice_channel: return "Eu não toco de graça, muito menos pro vácuo. Entre em um canal de voz."

    if state.voice_client is None or not state.voice_client.is_connected():
        state.voice_client = await voice_channel.connect(self_deaf=True)
        await asyncio.sleep(0.5)
    elif voice_channel != state.voice_client.channel:
        await state.voice_client.move_to(voice_channel)

    if isinstance(ctx_or_interaction, discord.Interaction):
        await ctx_or_interaction.response.defer()
    
    initial_message = None
    playlist_load_quotes = [
        "Carregando a playlist... Cada música aumenta minha comissão, só pra você saber.",
        "Segura aí… cada música tem que passar pelo meu teste de baixista profissional.",
        "Tocando mentalmente cada linha de baixo antes de colocar na fila… só pra garantir."
    ]
    if isinstance(ctx_or_interaction, discord.Interaction):
        initial_message = await ctx_or_interaction.followup.send(random.choice(playlist_load_quotes))
    else:
        initial_message = await channel.send(random.choice(playlist_load_quotes))

    playlist_info = await search_ytdlp(playlist_url, is_playlist=True, playlist_limit=limit)

    if not playlist_info or 'entries' not in playlist_info:
        return await initial_message.edit(content="Isso não é um link de uma playlist. Foco, por favor.")

    songs_added, skipped_count = 0, 0
    
    tasks = [search_ytdlp(entry['url']) for entry in playlist_info['entries'] if entry]
    results = await asyncio.gather(*tasks)

    for video_details in results:
        if video_details:
            song_data = {
                'audio_url': video_details.get('url'), 'webpage_url': video_details.get('webpage_url'),
                'title': video_details.get('title', 'Sem título'), 'duration': video_details.get('duration'),
                'requester': user
            }
            if song_data['audio_url']:
                state.queue.append(song_data)
                songs_added += 1
            else:
                skipped_count += 1
        else:
            skipped_count += 1
    
    playlist_title = playlist_info.get('title', 'essa aí')
    success_message = f"Pronto. **{songs_added}** músicas da playlist **'{playlist_title}'** foram adicionadas à fila. Agora vamos falar de negócios..."
    if skipped_count > 0: success_message += f"\n({skipped_count} vídeos indisponíveis foram pulados.)"
    
    await initial_message.edit(content=success_message)

    if not state.is_playing: await state.play_next_song()

async def loop_logic(guild):
    state = get_guild_state(guild.id, bot.loop)
    state.loop_enabled = not state.loop_enabled
    if state.loop_enabled:
        return "Loop da fila ativado. A festa (ou o sofrimento) não vai ter fim."
    else:
        return "Loop da fila desativado. Tudo que é bom (ou ruim) uma hora acaba."

async def pular_logic(guild):
    state = get_guild_state(guild.id, bot.loop)
    if not state.queue and not state.loop_enabled:
        skip_empty_quotes = [ "Fila vazia… o universo conspira para minha preguiça.", "Quer que eu pule o silêncio? Interessante... mas não." ]
        return random.choice(skip_empty_quotes)
    if state.voice_client and state.is_playing:
        state.voice_client.stop()
        skip_quotes = [
            "Pulei. O nome da música soa poético… mas próximo, por favor.", "Troquei. Às vezes a vida é só cortar caminho.",
            "Próxima faixa. Silêncio é caro, e eu não trabalho de graça.", "Seguindo em frente. Retroceder nunca foi meu estilo.",
            "Pulei. A propósito, o nome “Hitori Gotoh” soa como “monólogo” em japonês. E “Bocchi” vem de “sozinha”. Faz sentido, né? Enfim, próxima.",
            "Próxima. Você sabia que o “doritos” no cabelo da Nijika se move com as emoções dela? É estranhamente expressivo."
        ]
        return random.choice(skip_quotes)
    return "Quer que eu pule o silêncio? Interessante... mas não."

async def fila_logic(guild):
    state = get_guild_state(guild.id, bot.loop)
    embed = discord.Embed(title="🎵 Fila de Músicas", color=MAIN_COLOR)
    description = ""
    if state.current_song:
        song = state.current_song
        description += f"▶️ **Tocando agora:** {song['title']} [{format_duration(song['duration'])}]\n\n"
    if state.queue:
        description += "**Próximas na fila:**\n"
        for i, song in enumerate(list(state.queue)[:10]):
            description += f"`{i+1}.` {song['title']}\n"
    if not description:
        description = "A fila está vazia. Que paz..."
    embed.description = description
    loop_status = "✅ Ativado" if state.loop_enabled else "❌ Desativado"
    embed.set_footer(text=f"Loop: {loop_status}")
    return embed

async def parar_logic(guild):
    state = get_guild_state(guild.id, bot.loop)
    if state.voice_client and state.voice_client.is_connected():
        state.queue.clear()
        state.is_playing = False
        state.loop_enabled = False
        state.voice_client.stop()
        await state.voice_client.disconnect()
        if guild.id in GUILD_STATES: del GUILD_STATES[guild.id]
        stop_quotes = [
            "Show cancelado. Beber, fumar, curtir com a mulherada e usar um corte de cabelo tigelinha... essa é a vida do músico, e eu tenho que ir vivê-la. Fui.",
            "Ok, parei tudo. Meu contrato não incluía tocar de graça pra sempre."
        ]
        return random.choice(stop_quotes)
    return "Eu já não estava fazendo nada. Então, missão cumprida, eu acho."

async def pausar_logic(guild):
    state = get_guild_state(guild.id, bot.loop)
    if state.voice_client and state.voice_client.is_playing():
        state.voice_client.pause()
        return "Pausado. Como musicista por excelência que eu sou, sei que o silêncio também faz parte da música."
    return "Como eu vou pausar o que não começou? Pensa um pouco."

async def continuar_logic(guild):
    state = get_guild_state(guild.id, bot.loop)
    if state.voice_client and state.voice_client.is_paused():
        state.voice_client.resume()
        return "Ok, a pausa dramática acabou. Voltando ao som."
    return "Não tem nada pausado pra continuar. Simples assim."

async def tocandoagora_logic(guild):
    state = get_guild_state(guild.id, bot.loop)
    if state.current_song and state.is_playing:
        song = state.current_song
        embed = discord.Embed(title="Tocando agora", color=MAIN_COLOR)
        embed.description = f"**{song['title']}**\n`Duração: {format_duration(song['duration'])}`\n\nRequisitado por: {song['requester'].mention}"
        return embed
    return discord.Embed(description="Não tem nada tocando. Um silêncio bem agradável, aliás.", color=MAIN_COLOR)

def get_help_embed():
    description_text = """Esses comandos só funcionam porque eu toco impecavelmente, lembre-se disso.

`r!tocar:` A base perfeita pra qualquer música — eu, claro.
`r!playlist:` Quer um setlist inteiro? Jogue o link, eficiente e lucrativo.
`r!fila:` A fila dos impacientes. Veja quem vem depois.
`r!pular:` Quando a música não paga... digo, não vale o tempo, eu pulo.
`r!loop:` O mesmo som, de novo e de novo. Se você gostar de insistir.
`r!pausar:` Silêncio temporário. Às vezes é necessário.
`r!continuar:` Quando o silêncio já virou incômodo.
`r!parar:` Show encerrado. Simples assim.
`r!tocandoagora:` Caso você não lembre nem do que está ouvindo.
"""
    embed = discord.Embed(
        title="Ajuda da Ryo",
        description=description_text,
        color=MAIN_COLOR
    )
    embed.set_image(url="https://cdn.discordapp.com/attachments/1211290501505093713/1421558069128724691/PSX_20250927_145910.png?ex=68dd6d1e&is=68dc1b9e&hm=59c193373afaa1891bf6d5e1b89ab28be3b43db5d5e2ed6720cb7df22066370f")
    embed.set_footer(text="Utilize os comandos slash!")
    return embed

@bot.tree.command(name="tocar", description="Toca uma música ou a adiciona na fila.")
@app_commands.describe(musica="Nome ou link da música")
async def tocar_slash(interaction: discord.Interaction, musica: str):
    response = await tocar_logic(interaction, musica)
    if isinstance(response, str): await interaction.followup.send(response, ephemeral=True)

@bot.command(name="tocar", aliases=['p', 'play'])
async def tocar_prefix(ctx, *, song_query: str = None):
    if song_query is None: return await ctx.send("Você me chamou aqui pra quê exatamente? Diga o nome da música.")
    response = await tocar_logic(ctx, song_query)
    if isinstance(response, str): await ctx.send(response)

@bot.tree.command(name="playlist", description="Toca uma playlist.")
@app_commands.describe(link_da_playlist="O link completo da playlist", limite="Número de músicas para adicionar")
async def playlist_slash(interaction: discord.Interaction, link_da_playlist: str, limite: int = 0):
    response = await playlist_logic(interaction, link_da_playlist, limite)
    if isinstance(response, str): await interaction.followup.send(response, ephemeral=True)

@bot.command(name="playlist", aliases=['pl'])
async def playlist_prefix(ctx, playlist_url: str, limit: int = 0):
    response = await playlist_logic(ctx, playlist_url, limit)
    if isinstance(response, str): await ctx.send(response)

@bot.tree.command(name="loop", description="Ativa ou desativa a repetição da fila de músicas.")
async def loop_slash(interaction: discord.Interaction):
    await interaction.response.send_message(await loop_logic(interaction.guild))

@bot.command(name="loop", aliases=['l'])
async def loop_prefix(ctx):
    await ctx.send(await loop_logic(ctx.guild))

@bot.tree.command(name="pular", description="Pula para a próxima música da fila.")
async def pular_slash(interaction: discord.Interaction): await interaction.response.send_message(await pular_logic(interaction.guild))

@bot.command(name="pular", aliases=['s', 'skip'])
async def pular_prefix(ctx): await ctx.send(await pular_logic(ctx.guild))

@bot.tree.command(name="fila", description="Mostra as próximas músicas na fila.")
async def fila_slash(interaction: discord.Interaction): await interaction.response.send_message(embed=await fila_logic(interaction.guild))

@bot.command(name="fila", aliases=['q', 'queue'])
async def fila_prefix(ctx): await ctx.send(embed=await fila_logic(ctx.guild))

@bot.tree.command(name="parar", description="Para a música, limpa a fila e desconecta.")
async def parar_slash(interaction: discord.Interaction): await interaction.response.send_message(await parar_logic(interaction.guild))

@bot.command(name="parar", aliases=['stop', 'disconnect', 'dc'])
async def parar_prefix(ctx): await ctx.send(await parar_logic(ctx.guild))

@bot.tree.command(name="pausar", description="Pausa a música atual.")
async def pausar_slash(interaction: discord.Interaction): await interaction.response.send_message(await pausar_logic(interaction.guild))

@bot.command(name="pausar", aliases=['pause'])
async def pausar_prefix(ctx): await ctx.send(await pausar_logic(ctx.guild))

@bot.tree.command(name="continuar", description="Continua a tocar a música pausada.")
async def continuar_slash(interaction: discord.Interaction): await interaction.response.send_message(await continuar_logic(interaction.guild))

@bot.command(name="continuar", aliases=['resume'])
async def continuar_prefix(ctx): await ctx.send(await continuar_logic(ctx.guild))

@bot.tree.command(name="tocandoagora", description="Mostra qual música está tocando.")
async def tocandoagora_slash(interaction: discord.Interaction): await interaction.response.send_message(embed=await tocandoagora_logic(interaction.guild), ephemeral=True)

@bot.command(name="tocandoagora", aliases=['np', 'nowplaying'])
async def tocandoagora_prefix(ctx): await ctx.send(embed=await tocandoagora_logic(ctx.guild))

@bot.tree.command(name="ajuda", description="Mostra todos os comandos disponíveis.")
async def ajuda_slash(interaction: discord.Interaction): await interaction.response.send_message(embed=get_help_embed(), ephemeral=True)

@bot.command(name="ajuda", aliases=['help'])
async def ajuda_prefix(ctx): await ctx.send(embed=get_help_embed())

if __name__ == "__main__":
    keep_alive()
    bot.run(TOKEN)
