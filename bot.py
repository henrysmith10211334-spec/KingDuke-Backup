import discord
from discord import app_commands
import aiohttp
import gspread
from google.oauth2.service_account import Credentials
import json
import os
import re

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

# ── CONFIG ──────────────────────────────────────────────────────────────────
DISCORD_TOKEN   = os.environ.get("DISCORD_TOKEN") or os.environ.get("TOKEN")
GROQ_API_KEY    = os.environ.get("GROQ_API_KEY")
SPREADSHEET_ID  = os.environ.get("SPREADSHEET_ID")
OWNER_IDS       = {1364018193580163194, 805304956633481260}

# Google Sheets setup
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
SERVICE_ACCOUNT_INFO = json.loads(os.environ.get("GOOGLE_SERVICE_ACCOUNT"))
creds       = Credentials.from_service_account_info(SERVICE_ACCOUNT_INFO, scopes=SCOPES)
gc          = gspread.authorize(creds)
spreadsheet = gc.open_by_key(SPREADSHEET_ID)
speedups_sheet  = spreadsheet.worksheet("Speedups")
resources_sheet = spreadsheet.worksheet("Resources")

# ── BOT SETUP ────────────────────────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)
tree   = app_commands.CommandTree(client)

sleeping = False

# ── HELPERS ──────────────────────────────────────────────────────────────────
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

def get_sheet_from_category(category: str):
    if category == "speedups":
        return speedups_sheet
    elif category == "rss":
        return resources_sheet
    else:
        raise ValueError("Invalid category")

# ── FIXED OCR FUNCTION (NEW MODEL) ───────────────────────────────────────────
async def groq_read_image(image_url: str, prompt: str) -> str:
    payload = {
        "model": "llama-3.2-vision-preview",
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": prompt
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": image_url
                        }
                    }
                ]
            }
        ]
    }

    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(
            "https://api.groq.com/openai/v1/chat/completions",
            json=payload,
            headers=headers,
        ) as resp:

            data = await resp.json()

            print("GROQ RAW:", data)

            if "error" in data:
                raise Exception(f"OCR Error: {data['error'].get('message', 'Unknown error')}")

            return data["choices"][0]["message"]["content"]

async def groq_extract_generic(image_url: str, prompt: str) -> str:
    return await groq_read_image(image_url, prompt)

# ── PREFIX COMMANDS ──────────────────────────────────────────────────────────
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
            await message.channel.send(embed=make_embed("❌ You don't have permission to do that.", discord.Color.red()))

    elif message.content.lower() == ",startup":
        if is_owner:
            sleeping = False
            await message.channel.send("🟢 Bot is up and running again!")
        else:
            await message.channel.send(embed=make_embed("❌ You don't have permission to do that.", discord.Color.red()))

    elif message.content.lower() == ",status":
        if is_owner or is_admin:
            if sleeping:
                await message.channel.send(embed=make_embed("🔴 Bot is currently under maintenance.", discord.Color.red()))
            else:
                await message.channel.send(embed=make_embed("🟢 Bot is online and running.", discord.Color.green()))
        else:
            await message.channel.send(embed=make_embed("❌ You don't have permission to do that.", discord.Color.red()))

# ── SPEEDUPS COMMAND ──────────────────────────────────────────────────────────
@tree.command(name="speedups", description="Submit your ROK speedups screenshot")
@app_commands.describe(image="Your ROK speedups screenshot")
async def speedups(interaction: discord.Interaction, image: discord.Attachment):
    global sleeping
    global REPORT_CHANNEL_ID

    if REPORT_CHANNEL_ID is None:
        await interaction.response.send_message(
            embed=make_embed("⚠️ The report channel has not been set yet. An admin must run /setreportchannel.", discord.Color.yellow()),
            ephemeral=True
        )
        return

    if interaction.channel_id != REPORT_CHANNEL_ID:
        await interaction.response.send_message(
            embed=make_embed(f"❌ This command can only be used in <#{REPORT_CHANNEL_ID}>.", discord.Color.red()),
            ephemeral=True
        )
        return

    if sleeping:
        await interaction.response.send_message(
            embed=make_embed("❌ Bot is currently under maintenance.", discord.Color.red()),
            ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)

    try:
        raw = await groq_read_image(
            image.url,
            "From this ROK screenshot extract Healing and Universal speedup times. Reply ONLY: HEALING:Xd Xh Xm UNIVERSAL:Xd Xh Xm"
        )
    except Exception:
        await interaction.followup.send(embed=make_embed("❌ Failed to send report, please try again or report to bot owner.", discord.Color.red()), ephemeral=True)
        return

    try:
        healing   = raw.split("UNIVERSAL:")[0].replace("HEALING:", "").strip()
        universal = raw.split("UNIVERSAL:")[1].strip()
    except Exception:
        await interaction.followup.send(embed=make_embed("❌ Failed to send report, please try again or report to bot owner.", discord.Color.red()), ephemeral=True)
        return

    display_name = strip_fancy(interaction.user.display_name) or interaction.user.name

    try:
        existing_row = find_row(speedups_sheet, display_name)
        if existing_row > -1:
            speedups_sheet.update(f"B{existing_row}:D{existing_row}", [[display_name, healing, universal]])
            await interaction.followup.send(embed=make_embed("⚠️ You have already sent a report, your previous report has been replaced by this one.", discord.Color.yellow()), ephemeral=True)
        else:
            speedups_sheet.append_row(["", display_name, healing, universal])
            await interaction.followup.send(embed=make_embed("✅ Your speedups report has been sent!", discord.Color.green()), ephemeral=True)
    except Exception:
        await interaction.followup.send(embed=make_embed("❌ Failed to send report, please try again or report to bot owner.", discord.Color.red()), ephemeral=True)

# ── RESOURCES COMMAND ─────────────────────────────────────────────────────────
@tree.command(name="rss", description="Submit your ROK rss screenshot")
@app_commands.describe(image="Your ROK resources screenshot")
async def resources(interaction: discord.Interaction, image: discord.Attachment):
    global sleeping
    global REPORT_CHANNEL_ID

    if REPORT_CHANNEL_ID is None:
        await interaction.response.send_message(
            embed=make_embed("⚠️ The report channel has not been set yet. An admin must run /setreportchannel.", discord.Color.yellow()),
            ephemeral=True
        )
        return

    if interaction.channel_id != REPORT_CHANNEL_ID:
        await interaction.response.send_message(
            embed=make_embed(f"❌ This command can only be used in <#{REPORT_CHANNEL_ID}>.", discord.Color.red()),
            ephemeral=True
        )
        return

    if sleeping:
        await interaction.response.send_message(
            embed=make_embed("❌ Bot is currently under maintenance.", discord.Color.red()),
            ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)

    try:
        raw = await groq_read_image(
            image.url,
            "Extract FOOD, WOOD, STONE, GOLD. Reply ONLY: FOOD:X WOOD:X STONE:X GOLD:X"
        )
    except Exception:
        await interaction.followup.send(embed=make_embed("❌ Failed to send report, please try again or report to bot owner.", discord.Color.red()), ephemeral=True)
        return

    try:
        food  = raw.split("WOOD:")[0].replace("FOOD:", "").strip()
        wood  = raw.split("WOOD:")[1].split("STONE:")[0].strip()
        stone = raw.split("STONE:")[1].split("GOLD:")[0].strip()
        gold  = raw.split("GOLD:")[1].strip()
    except Exception:
        await interaction.followup.send(embed=make_embed("❌ Failed to parse OCR output.", discord.Color.red()), ephemeral=True)
        return

    display_name = strip_fancy(interaction.user.display_name) or interaction.user.name

    try:
        existing_row = find_row(resources_sheet, display_name)
        if existing_row > -1:
            resources_sheet.update(f"B{existing_row}:F{existing_row}", [[display_name, food, wood, stone, gold]])
            await interaction.followup.send(embed=make_embed("⚠️ You have already sent a report, your previous report has been replaced by this one.", discord.Color.yellow()), ephemeral=True)
        else:
            resources_sheet.append_row(["", display_name, food, wood, stone, gold])
            await interaction.followup.send(embed=make_embed("✅ Your rss report has been sent!", discord.Color.green()), ephemeral=True)
    except Exception:
        await interaction.followup.send(embed=make_embed("❌ Failed to send report, please try again or report to bot owner.", discord.Color.red()), ephemeral=True)

# ── OWNER-ONLY ADDREPORT COMMAND ─────────────────────────────────────────────
@tree.command(name="addreport", description="Owner-only: Add a report for another user using an image")
@app_commands.describe(
    category="Choose report type",
    user="Select the user to assign the report to",
    image="Upload the screenshot"
)
@app_commands.choices(category=[
    app_commands.Choice(name="Speedups", value="speedups"),
    app_commands.Choice(name="RSS", value="rss")
])
async def addreport(interaction: discord.Interaction, category: app_commands.Choice[str], user: discord.Member, image: discord.Attachment):

    if interaction.user.id not in OWNER_IDS:
        await interaction.response.send_message(
            embed=make_embed("❌ Only bot owners can use this command.", discord.Color.red()),
            ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)

    if category.value == "speedups":
        prompt = (
            "Extract Healing and Universal speedup times. Reply ONLY: HEALING:Xd Xh Xm UNIVERSAL:Xd Xh Xm"
        )
    else:
        prompt = (
            "Extract FOOD, WOOD, STONE, GOLD. Reply ONLY: FOOD:X WOOD:X STONE:X GOLD:X"
        )

    try:
        raw = await groq_read_image(image.url, prompt)
    except Exception:
        await interaction.followup.send(
            embed=make_embed("❌ OCR failed.", discord.Color.red()),
            ephemeral=True
        )
        return

    try:
        if category.value == "speedups":
            healing   = raw.split("UNIVERSAL:")[0].replace("HEALING:", "").strip()
            universal = raw.split("UNIVERSAL:")[1].strip()
            parsed = [healing, universal]
        else:
            food  = raw.split("WOOD:")[0].replace("FOOD:", "").strip()
            wood  = raw.split("WOOD:")[1].split("STONE:")[0].strip()
            stone = raw.split("STONE:")[1].split("GOLD:")[0].strip()
            gold  = raw.split("GOLD:")[1].strip()
            parsed = [food, wood, stone, gold]
    except Exception:
        await interaction.followup.send(
            embed=make_embed("❌ Failed to parse OCR output.", discord.Color.red()),
            ephemeral=True
        )
        return

    sheet = get_sheet_from_category(category.value)
    display_name = strip_fancy(user.display_name) or user.name

    try:
        if category.value == "speedups":
            sheet.append_row(["", display_name, parsed[0], parsed[1]])
        else:
            sheet.append_row(["", display_name, parsed[0], parsed[1], parsed[2], parsed[3]])

        await interaction.followup.send(
            embed=make_embed(
                f"✅ Added {category.name} report for **{display_name}**.",
                discord.Color.green()
            ),
            ephemeral=True
        )
    except Exception:
        await interaction.followup.send(
            embed=make_embed("❌ Failed to write to sheet.", discord.Color.red()),
            ephemeral=True
        )

# ── OWNER-ONLY DELETEREPORT COMMAND ──────────────────────────────────────────
@tree.command(name="deletereport", description="Owner-only: Delete a report row")
@app_commands.describe(
    category="Choose report type",
    row="Row number to delete"
)
@app_commands.choices(category=[
    app_commands.Choice(name="Speedups", value="speedups"),
    app_commands.Choice(name="RSS", value="rss")
])
async def deletereport(interaction: discord.Interaction, category: app_commands.Choice[str], row: int):

    if interaction.user.id not in OWNER_IDS:
        await interaction.response.send_message(
            embed=make_embed("❌ Only bot owners can use this command.", discord.Color.red()),
            ephemeral=True
        )
        return

    sheet = get_sheet_from_category(category.value)

    try:
        sheet.delete_rows(row)
        await interaction.response.send_message(
            embed=make_embed(
                f"🗑️ Deleted row **{row}** from **{category.name}**.",
                discord.Color.green()
            ),
            ephemeral=True
        )
    except Exception as e:
        await interaction.response.send_message(
            embed=make_embed(f"❌ Error: {e}", discord.Color.red()),
            ephemeral=True
        )

# ── SET REPORT CHANNEL COMMAND ───────────────────────────────────────────────
@tree.command(name="setreportchannel", description="Set the channel where /speedups and /rss are allowed")
@app_commands.describe(channel="The channel to allow /speedups and /rss in")
async def setreportchannel(interaction: discord.Interaction, channel: discord.TextChannel):
    global REPORT_CHANNEL_ID, config

    if not interaction.user.guild_permissions.administrator and interaction.user.id not in OWNER_IDS:
        await interaction.response.send_message(embed=make_embed("❌ You don't have permission to use this command.", discord.Color.red()), ephemeral=True)
        return

    REPORT_CHANNEL_ID = channel.id
    config["report_channel_id"] = REPORT_CHANNEL_ID
    save_config(config)

    await interaction.response.send_message(
        embed=make_embed(f"✅ /speedups and /rss are now restricted to <#{REPORT_CHANNEL_ID}>.", discord.Color.green()),
        ephemeral=True
    )

# ── RUN ──────────────────────────────────────────────────────────────────────
@client.event
async def on_ready():
    await tree.sync()
    print(f"Logged in as {client.user}")

client.run(DISCORD_TOKEN)
