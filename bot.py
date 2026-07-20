import asyncio
import discord
from discord import app_commands
import gspread
from google.oauth2.service_account import Credentials
import json
import os
import re
 
from google.genai import Client
 
CONFIG_FILE = "config.json"
 
def load_config():
    try:
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    except:
        return {"report_channel_id": None}
 
def save_config(data):
    with open(CONFIG_FILE, "w") as f:
        json.dump(data, f, indent=4)
 
config = load_config()
REPORT_CHANNEL_ID = config.get("report_channel_id")
 
DISCORD_TOKEN   = os.environ.get("DISCORD_TOKEN") or os.environ.get("TOKEN")
SPREADSHEET_ID  = os.environ.get("SPREADSHEET_ID")
OWNER_IDS       = {1364018193580163194, 805304956633481260}
 
# Google Sheets setup
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
SERVICE_ACCOUNT_INFO = json.loads(os.environ.get("GOOGLE_SERVICE_ACCOUNT"))
creds       = Credentials.from_service_account_info(SERVICE_ACCOUNT_INFO, scopes=SCOPES)
gc          = gspread.authorize(creds)
spreadsheet = gc.open_by_key(SPREADSHEET_ID)
speedups_sheet  = spreadsheet.worksheet("Speedups")
 
# Gemini setup
gemini = Client(api_key=os.environ["GEMINI_API_KEY"])
 
# Discord bot setup
intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)
tree   = app_commands.CommandTree(client)
 
sleeping = False
 
# ───────────────────────────────────────────────────────────────
# Fallback auto-detection (regex)
# ───────────────────────────────────────────────────────────────
def fallback_extract(text: str):
    clean = text.replace("\n", " ").replace("\r", " ")
 
    heal_match = re.search(r"Healing Speedup\s*([0-9dhm ,]+)", clean, re.I)
    healing = heal_match.group(1).strip() if heal_match else "0"
 
    uni_match = re.search(r"Universal Speedup\s*([0-9dhm ,]+)", clean, re.I)
    universal = uni_match.group(1).strip() if uni_match else "0"
 
    return healing, universal
 
# ───────────────────────────────────────────────────────────────
# Gemini OCR with JSON + fallback (dict-based contents)
# ───────────────────────────────────────────────────────────────
async def gemini_ocr(image: discord.Attachment):
    try:
        img_bytes = await image.read()
 
        prompt = """
        Extract ONLY the following two values from the image:
 
        - Healing Speedup total duration
        - Universal Speedup total duration
 
        Return STRICT JSON ONLY:
        {
          "healing": "<value>",
          "universal": "<value>"
        }
 
        No explanations.
        No extra text.
        No <think>.
        If a value is missing, set it to "0".
        """
 
        response = None
        max_retries = 3
        for attempt in range(max_retries):
            try:
                response = gemini.models.generate_content(
                    model="gemini-3.5-flash",
                    contents=[
                        {
                            "role": "user",
                            "parts": [
                                {"text": prompt},
                                {
                                    "inline_data": {
                                        "mime_type": image.content_type,
                                        "data": img_bytes,
                                    }
                                },
                            ],
                        }
                    ],
                )
                break
            except Exception as e:
                overloaded = "503" in str(e) or "UNAVAILABLE" in str(e)
                if overloaded and attempt < max_retries - 1:
                    wait = 2 ** attempt  # 1s, 2s, 4s
                    print(f"⏳ Gemini overloaded, retrying in {wait}s (attempt {attempt + 1}/{max_retries})")
                    await asyncio.sleep(wait)
                    continue
                raise
 
        # Extract text from response
        parts = response.candidates[0].content.parts
        raw = "".join(
            getattr(p, "text", "") for p in parts if hasattr(p, "text") and p.text
        ).strip()
 
        print("🟢 GEMINI RAW:", raw)
 
        match = re.search(r"\{.*\}", raw, flags=re.S)
        if match:
            try:
                data = json.loads(match.group(0))
                healing = data.get("healing", "0")
                universal = data.get("universal", "0")
                if healing != "0" or universal != "0":
                    return healing, universal
            except:
                pass
 
        print("⚠️ JSON failed → using fallback detection")
        healing, universal = fallback_extract(raw)
        return healing, universal
 
    except Exception as e:
        print("❌ Gemini OCR failed:", repr(e))
        return None
 
# ───────────────────────────────────────────────────────────────
# Helpers
# ───────────────────────────────────────────────────────────────
def strip_fancy(text: str) -> str:
    return re.sub(r'[^\x00-\x7F]+', '', text).strip()
 
def find_row(sheet, username: str):
    col_b = sheet.col_values(2)
    for i, val in enumerate(col_b):
        if val.strip().lower() == username.strip().lower():
            return i + 1
    return -1
 
def make_embed(description: str, color: discord.Color) -> discord.Embed:
    return discord.Embed(description=description, color=color)
 
# ───────────────────────────────────────────────────────────────
# Prefix commands
# ───────────────────────────────────────────────────────────────
@client.event
async def on_message(message):
    global sleeping
 
    if message.author.bot:
        return
 
    is_owner = message.author.id in OWNER_IDS
    is_admin = message.author.guild_permissions.administrator if message.guild else False
 
    if message.content.lower() == ",shutdown":
        if is_owner:
            sleeping = True
            await message.channel.send("🔴 Bot has been shutdown!")
        else:
            await message.channel.send(embed=make_embed("❌ You don't have permission.", discord.Color.red()))
 
    elif message.content.lower() == ",startup":
        if is_owner:
            sleeping = False
            await message.channel.send("🟢 Bot is up and running again!")
        else:
            await message.channel.send(embed=make_embed("❌ You don't have permission.", discord.Color.red()))
 
    elif message.content.lower() == ",status":
        if is_owner or is_admin:
            if sleeping:
                await message.channel.send(embed=make_embed("🔴 Bot is under maintenance.", discord.Color.red()))
            else:
                await message.channel.send(embed=make_embed("🟢 Bot is online.", discord.Color.green()))
        else:
            await message.channel.send(embed=make_embed("❌ You don't have permission.", discord.Color.red()))
 
# ───────────────────────────────────────────────────────────────
# Slash command: /speedups
# ───────────────────────────────────────────────────────────────
@tree.command(name="speedups", description="Submit your ROK speedups screenshot")
@app_commands.describe(image="Your ROK speedups screenshot")
async def speedups(interaction: discord.Interaction, image: discord.Attachment):
    global sleeping
    global REPORT_CHANNEL_ID
 
    if REPORT_CHANNEL_ID is None:
        await interaction.response.send_message(
            embed=make_embed("⚠️ Report channel not set.", discord.Color.yellow()),
            ephemeral=True
        )
        return
 
    if interaction.channel_id != REPORT_CHANNEL_ID:
        await interaction.response.send_message(
            embed=make_embed(f"❌ Use this only in <#{REPORT_CHANNEL_ID}>.", discord.Color.red()),
            ephemeral=True
        )
        return
 
    if sleeping:
        await interaction.response.send_message(
            embed=make_embed("❌ Bot under maintenance.", discord.Color.red()),
            ephemeral=True
        )
        return
 
    await interaction.response.defer(ephemeral=True)
 
    result = await gemini_ocr(image)
    if not result:
        await interaction.followup.send(embed=make_embed("❌ OCR failed.", discord.Color.red()), ephemeral=True)
        return
 
    healing, universal = result
    healing = healing or "0"
    universal = universal or "0"
 
    display_name = strip_fancy(interaction.user.display_name) or interaction.user.name
 
    try:
        existing_row = find_row(speedups_sheet, display_name)
        if existing_row > -1:
            result = speedups_sheet.update(f"B{existing_row}:D{existing_row}", [[display_name, healing, universal]])
            print(f"📍 Sheet UPDATE landed at: {result.get('updatedRange')}")
            await interaction.followup.send(embed=make_embed("⚠️ Updated previous report.", discord.Color.yellow()), ephemeral=True)
        else:
            next_row = len(speedups_sheet.col_values(2)) + 1
            result = speedups_sheet.update(f"B{next_row}:D{next_row}", [[display_name, healing, universal]])
            print(f"📍 Sheet APPEND landed at: {result.get('updatedRange')}")
            await interaction.followup.send(embed=make_embed("✅ Report submitted!", discord.Color.green()), ephemeral=True)
    except Exception as e:
        print("❌ Sheet write failed:", repr(e))
        await interaction.followup.send(embed=make_embed("❌ Sheet write failed.", discord.Color.red()), ephemeral=True)
 
# ───────────────────────────────────────────────────────────────
# Set report channel
# ───────────────────────────────────────────────────────────────
@tree.command(name="setreportchannel", description="Set the channel where /speedups is allowed")
@app_commands.describe(channel="Channel to allow reports in")
async def setreportchannel(interaction: discord.Interaction, channel: discord.TextChannel):
    global REPORT_CHANNEL_ID, config
 
    if not interaction.user.guild_permissions.administrator and interaction.user.id not in OWNER_IDS:
        await interaction.response.send_message(embed=make_embed("❌ No permission.", discord.Color.red()), ephemeral=True)
        return
 
    REPORT_CHANNEL_ID = channel.id
    config["report_channel_id"] = REPORT_CHANNEL_ID
    save_config(config)
 
    await interaction.response.send_message(
        embed=make_embed(f"✅ Reports now restricted to <#{REPORT_CHANNEL_ID}>.", discord.Color.green()),
        ephemeral=True
    )
 
# ───────────────────────────────────────────────────────────────
# Run bot
# ───────────────────────────────────────────────────────────────
@client.event
async def on_ready():
    await tree.sync()
    print(f"Logged in as {client.user}")
 
client.run(DISCORD_TOKEN)
