import discord
from discord import app_commands
import gspread
from google.oauth2.service_account import Credentials
import json
import os
import re
from groq import Groq

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

# Groq client
groq_client = Groq(api_key=os.environ["GROQ_API_KEY"])

# Discord bot setup
intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)
tree   = app_commands.CommandTree(client)

sleeping = False

# ───────────────────────────────────────────────────────────────
# QWEN OCR — ONLY extract Healing + Universal
# ───────────────────────────────────────────────────────────────
async def qwen_ocr(image_url: str) -> tuple[str, str] | None:
    try:
        completion = groq_client.chat.completions.create(
            model="qwen/qwen3.6-27b",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "Extract ONLY the following two values from this image:\n\n"
                                "1. Healing Speedup — return ONLY the duration number (example: 11d 4h 40m)\n"
                                "2. Universal Speedup — return ONLY the duration number (example: 3,476d 17h 56m)\n\n"
                                "Do NOT extract any other text.\n"
                                "Do NOT describe the image.\n"
                                "Do NOT list anything.\n"
                                "Do NOT think.\n"
                                "Do NOT summarize.\n"
                                "Return ONLY the two raw values, each on its own line.\n"
                                "If a value is missing, return an empty line."
                            )
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": image_url}
                        }
                    ]
                }
            ],
            temperature=0,
            max_completion_tokens=128
        )

        raw = completion.choices[0].message.content.strip()
        print("🟢 OCR RAW OUTPUT:", raw)

        lines = raw.splitlines()
        healing = lines[0].strip() if len(lines) > 0 else ""
        universal = lines[1].strip() if len(lines) > 1 else ""

        return healing, universal

    except Exception as e:
        print("❌ OCR failed:", repr(e))
        return None

# ───────────────────────────────────────────────────────────────
# HELPERS
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
# SPEEDUPS COMMAND — ONLY Healing + Universal
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

    result = await qwen_ocr(image.url)
    if not result:
        await interaction.followup.send(embed=make_embed("❌ OCR failed.", discord.Color.red()), ephemeral=True)
        return

    healing, universal = result

    # fallback if empty
    healing = healing or "0"
    universal = universal or "0"

    display_name = strip_fancy(interaction.user.display_name) or interaction.user.name

    try:
        existing_row = find_row(speedups_sheet, display_name)
        if existing_row > -1:
            speedups_sheet.update(f"B{existing_row}:D{existing_row}", [[display_name, healing, universal]])
            await interaction.followup.send(embed=make_embed("⚠️ Updated previous report.", discord.Color.yellow()), ephemeral=True)
        else:
            speedups_sheet.append_row(["", display_name, healing, universal])
            await interaction.followup.send(embed=make_embed("✅ Report submitted!", discord.Color.green()), ephemeral=True)
    except Exception as e:
        print("❌ Sheet write failed:", repr(e))
        await interaction.followup.send(embed=make_embed("❌ Sheet write failed.", discord.Color.red()), ephemeral=True)

# ───────────────────────────────────────────────────────────────
# SET REPORT CHANNEL
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
# RUN BOT
# ───────────────────────────────────────────────────────────────
@client.event
async def on_ready():
    await tree.sync()
    print(f"Logged in as {client.user}")

client.run(DISCORD_TOKEN)
