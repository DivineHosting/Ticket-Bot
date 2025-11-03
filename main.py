import discord
from discord import app_commands
from discord.ext import commands
import json
import logging
import os
import io
from flask import Flask, request, abort, render_template_string
import threading
import secrets
from datetime import datetime, timedelta
import re

# Set up logging for debugging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Configure bot intents
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
client = commands.Bot(command_prefix=None, intents=intents)

# Flask setup for web server
app = Flask(__name__)
# Store transcripts and tokens in memory (cleared on bot restart)
transcripts = {}
tokens = {}

# Ticket system variables
TICKET_COUNTER_FILE = "ticket_counter.json"
TICKET_DATA_FILE = "ticket_data.json"
SUPPORT_PANEL_FILE = "support_panel.json"

# Load and save ticket counter
def load_ticket_counter():
    try:
        with open(TICKET_COUNTER_FILE, "r") as f:
            counter = json.load(f).get("counter", 0)
            return counter
    except FileNotFoundError:
        save_ticket_counter(0)
        return 0

def save_ticket_counter(counter):
    with open(TICKET_COUNTER_FILE, "w") as f:
        json.dump({"counter": counter}, f)

# Load and save ticket data
def load_ticket_data():
    try:
        with open(TICKET_DATA_FILE, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}

def save_ticket_data(data):
    with open(TICKET_DATA_FILE, "w") as f:
        json.dump(data, f)

# Load and save support panel data
def load_support_panel():
    try:
        with open(SUPPORT_PANEL_FILE, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}

def save_support_panel(data):
    with open(SUPPORT_PANEL_FILE, "w") as f:
        json.dump(data, f)

ticket_counter = load_ticket_counter()
ticket_data = load_ticket_data()
support_panel_data = load_support_panel()

async def log_action(client, title, fields, channel_id, url=None):
    if not channel_id:
        return
    log_channel = client.get_channel(channel_id)
    if not log_channel:
        logger.error(f"Log channel with ID {channel_id} not found!")
        return
    embed = discord.Embed(title=title, color=discord.Color.red(), timestamp=discord.utils.utcnow())
    for name, value in fields.items():
        embed.add_field(name=name, value=str(value) if value else "N/A", inline=False)
    if url:
        embed.add_field(name="Transcript", value=f"[View Transcript]({url})", inline=False)
    try:
        await log_channel.send(embed=embed)
    except Exception as e:
        logger.error(f"Failed to send log: {str(e)}")

async def generate_transcript(channel, ticket_number):
    messages = []
    message_count = 0
    embed_count = 0
    component_count = 0
    opened_at = None
    closed_at = None

    # Retrieve stored buttons for this ticket
    ticket_info = ticket_data.get(str(ticket_number), {})
    initial_message_buttons = ticket_info.get("initial_message_buttons", [])
    confirmation_message_buttons = ticket_info.get("confirmation_message_buttons", [])
    initial_message_id = ticket_info.get("initial_message_id")
    confirmation_message_id = ticket_info.get("confirmation_message_id")

    async for message in channel.history(limit=100, oldest_first=True):
        message_count += 1
        if not opened_at:
            opened_at = message.created_at
        if hasattr(discord.MessageType, 'pins') and message.type == discord.MessageType.pins:
            continue
        content = message.content
        has_content = content and content.strip()
        if has_content:
            user_mention_pattern = r"<@!?(\d+)>"
            for user_id in re.findall(user_mention_pattern, content):
                member = channel.guild.get_member(int(user_id))
                username = member.display_name if member else f"UnknownUser({user_id})"
                content = content.replace(f"<@!{user_id}>", f"@{username}").replace(f"<@{user_id}>", f"@{username}")
            role_mention_pattern = r"<@&(\d+)>"
            for role_id in re.findall(role_mention_pattern, content):
                role = channel.guild.get_role(int(role_id))
                role_name = role.name if role else f"UnknownRole({role_id})"
                content = content.replace(f"<@&{role_id}>", f"@{role_name}")
        # Get the user's top role color
        member = channel.guild.get_member(message.author.id)
        role_color = "#ffffff"  # Default to white if no role color
        if member:
            top_role = None
            for role in sorted(member.roles, key=lambda r: r.position, reverse=True):
                if role.color.value != 0:
                    top_role = role
                    break
            if top_role:
                role_color = f"#{top_role.color.value:06x}"
        adjusted_time = message.created_at + timedelta(hours=4)
        msg_data = {
            "display_name": message.author.display_name,
            "role_color": role_color,
            "timestamp": adjusted_time.strftime("%B %d, %Y, %I:%M %p"),
            "avatar_url": message.author.avatar.url if message.author.avatar else message.author.default_avatar.url
        }
        if has_content:
            msg_data["content"] = content
        if message.embeds:
            embed_count += len(message.embeds)
            embed_content = []
            for embed in message.embeds:
                embed_color = embed.color.value if embed.color else 0x43B581
                description_lines = embed.description.split('\n') if embed.description else []
                formatted_content = f"<strong>{embed.title or ''}</strong>"
                general_lines = [line for line in description_lines if not line.startswith("**Notice:**") and not line.startswith("•")]
                notice_lines = [line for line in description_lines if line.startswith("**Notice:**")]
                bullet_lines = [line for line in description_lines if line.startswith("•")]
                if general_lines:
                    formatted_content += '<ul><li>' + '</li><li>'.join(general_lines) + '</li></ul>'
                if notice_lines or bullet_lines:
                    formatted_content += "<strong>Notice:</strong><ul>"
                    notice_content = []
                    if notice_lines:
                        notice_content.extend(line.replace("**Notice:**", "").strip().split('\n') for line in notice_lines)
                    if bullet_lines:
                        notice_content.extend(line.replace("•", "").strip() for line in bullet_lines)
                    formatted_content += '<li>' + '</li><li>'.join(item for sublist in notice_content for item in (sublist if isinstance(sublist, list) else [sublist]) if item) + '</li></ul>'
                embed_content.append((formatted_content, embed_color))
            if embed_content:
                msg_data["embeds"] = embed_content
        if str(message.id) == initial_message_id and initial_message_buttons:
            msg_data["buttons"] = initial_message_buttons
            component_count += len(initial_message_buttons)
            logger.debug(f"Added initial message buttons for ticket {ticket_number}: {initial_message_buttons}")
        elif str(message.id) == confirmation_message_id and confirmation_message_buttons:
            msg_data["buttons"] = confirmation_message_buttons
            component_count += len(confirmation_message_buttons)
            logger.debug(f"Added confirmation message buttons for ticket {ticket_number}: {confirmation_message_buttons}")
        elif message.components:
            component_count += sum(len(row.children) for row in message.components)
            buttons = []
            for component in message.components:
                for child in component.children:
                    if isinstance(child, discord.ui.Button):
                        emoji = str(child.emoji) if child.emoji else ""
                        label = child.label or "Unnamed"
                        buttons.append(f"{emoji} {label}".strip())
            if buttons:
                msg_data["buttons"] = buttons
                logger.debug(f"Fallback: Captured buttons for ticket {ticket_number} at message {message.id}: {buttons}")
        messages.append(msg_data)
        if "closed" in channel.name and not closed_at:
            closed_at = message.created_at
    return {
        "messages": messages,
        "stats": {
            "opened_at": (opened_at + timedelta(hours=4)).strftime("%m/%d/%Y, %H:%M:%S") if opened_at else "N/A",
            "closed_at": (closed_at + timedelta(hours=4)).strftime("%m/%d/%Y, %H:%M:%S") if closed_at else "N/A",
            "creator": ticket_data.get(str(ticket_number), {}).get("creator_id"),
            "closer": ticket_data.get(str(ticket_number), {}).get("closer_id"),
            "message_count": message_count,
            "embed_count": embed_count,
            "component_count": component_count,
            "server_name": channel.guild.name
        }
    }

class SupportButton(discord.ui.Button):
    def __init__(self, staff_role_id, ticket_category_id, ticket_log_channel_id, label="Create Support Ticket"):
        super().__init__(style=discord.ButtonStyle.green, label=label, emoji="📬", custom_id="support_button")
        self.staff_role_id = staff_role_id
        self.ticket_category_id = ticket_category_id
        self.ticket_log_channel_id = ticket_log_channel_id

    async def callback(self, interaction: discord.Interaction):
        # Defer the interaction response to avoid timeout
        await interaction.response.defer(ephemeral=True)

        for ticket_id, ticket_info in ticket_data.items():
            if ticket_info.get("creator_id") == interaction.user.id and not ticket_info.get("closer_id"):
                await interaction.followup.send("You already have an open ticket! Please wait until it is closed before creating a new one.", ephemeral=True)
                return

        global ticket_counter
        ticket_counter += 1
        save_ticket_counter(ticket_counter)

        guild = interaction.guild
        server_name = guild.name
        ticket_category = guild.get_channel(self.ticket_category_id) if self.ticket_category_id else None
        ticket_channel = await guild.create_text_channel(
            f"ticket-{ticket_counter}",
            category=ticket_category,
            overwrites={
                guild.default_role: discord.PermissionOverwrite(view_channel=False),
                interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True),
                guild.get_role(self.staff_role_id): discord.PermissionOverwrite(view_channel=True, send_messages=True),
                guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True)
            }
        )

        ticket_data[str(ticket_counter)] = {
            "creator_id": interaction.user.id,
            "staff_role_id": self.staff_role_id,
            "ticket_log_channel_id": self.ticket_log_channel_id,
            "ticket_category_id": self.ticket_category_id,
            "closed_tickets_category_id": support_panel_data.get(str(interaction.guild.id), {}).get("closed_tickets_category_id")
        }

        creator = interaction.guild.get_member(ticket_data[str(ticket_counter)].get("creator_id"))
        creator_text = creator.display_name if creator else "N/A"

        await log_action(interaction.client, f"Ticket Created", {
            "Created By": creator_text,
            "Claimed By": "N/A",
            "Closed By": "N/A",
            "Ticket": f"ticket-{ticket_counter}",
            "Channel": ticket_channel.mention
        }, self.ticket_log_channel_id)

        guild_id = str(interaction.guild.id)
        panel_data = support_panel_data.get(guild_id, {})
        embed = discord.Embed(
            title=panel_data.get("embed_title", f"{server_name} Support Ticket"),
            description=panel_data.get("embed_description", (
                "Greetings! You’ve reached our support hub.\n"
                "Press the button below to start a ticket, and our dedicated team will guide you through any issues.\n\n"
                "**Tips:** Stay polite, share clear details, and avoid spamming—help is on the way!"
            )),
            color=panel_data.get("embed_color", 0x00FFFF)
        )
        image_url = panel_data.get("image")
        if image_url:
            logger.debug(f"Attempting to set image for ticket embed: {image_url}")
            if (image_url.startswith("http://") or image_url.startswith("https://")) and any(image_url.lower().endswith(ext) for ext in ['.png', '.jpg', '.jpeg', '.gif']):
                embed.set_image(url=image_url)
            else:
                logger.warning(f"Invalid image URL in panel_data: {image_url}")
        else:
            logger.debug("No image URL found in panel_data.")
        embed.set_footer(text="🎫 Support")
        view = TicketView(interaction.user.id, ticket_counter, self.staff_role_id, self.ticket_log_channel_id)
        initial_buttons = ["📩 Claim Ticket", "🔒 Close Ticket"]
        message = await ticket_channel.send(embed=embed, view=view)
        ticket_data[str(ticket_counter)]["initial_message_id"] = str(message.id)
        ticket_data[str(ticket_counter)]["initial_message_buttons"] = initial_buttons
        save_ticket_data(ticket_data)
        logger.debug(f"Stored initial buttons for ticket {ticket_counter}: {initial_buttons}")

        staff_role = guild.get_role(self.staff_role_id)
        await ticket_channel.send(f"{staff_role.mention} {interaction.user.mention}")

        await interaction.followup.send(f"Your ticket has been created: {ticket_channel.mention}", ephemeral=True)

class SupportView(discord.ui.View):
    def __init__(self, staff_role_id, ticket_category_id, ticket_log_channel_id, button_label="Create Support Ticket"):
        super().__init__(timeout=None)
        self.add_item(SupportButton(staff_role_id, ticket_category_id, ticket_log_channel_id, button_label))

class TicketView(discord.ui.View):
    def __init__(self, ticket_creator_id, ticket_number, staff_role_id, ticket_log_channel_id):
        super().__init__(timeout=None)
        self.ticket_creator_id = ticket_creator_id
        self.ticket_number = ticket_number
        self.staff_role_id = staff_role_id
        self.ticket_log_channel_id = ticket_log_channel_id

    @discord.ui.button(style=discord.ButtonStyle.green, label="Claim Ticket", emoji="📩", custom_id="claim_ticket")
    async def claim_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        staff_role = interaction.guild.get_role(self.staff_role_id)
        if staff_role not in interaction.user.roles:
            await interaction.response.send_message("You do not have permission to claim this ticket. This action is restricted to staff members only.", ephemeral=True)
            return

        ticket_info = ticket_data.get(str(self.ticket_number), {})
        if "claimer_id" in ticket_info:
            claimer = interaction.guild.get_member(ticket_info["claimer_id"])
            if claimer:
                embed = discord.Embed(
                    description=f"This ticket has already been claimed by {claimer.display_name}.",
                    color=discord.Color.red()
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return

        ticket_data[str(self.ticket_number)]["claimer_id"] = interaction.user.id
        save_ticket_data(ticket_data)

        overwrites = interaction.channel.overwrites
        for member in interaction.guild.members:
            if staff_role in member.roles and member.id != interaction.user.id:
                if not any(role.permissions.administrator for role in member.roles):
                    overwrites[member] = discord.PermissionOverwrite(view_channel=False)
        overwrites[interaction.user] = discord.PermissionOverwrite(view_channel=True, send_messages=True)
        overwrites[interaction.guild.get_member(self.ticket_creator_id)] = discord.PermissionOverwrite(view_channel=True, send_messages=True)
        await interaction.channel.edit(overwrites=overwrites)

        embed = discord.Embed(description=f"This ticket has been claimed by {interaction.user.display_name}.", color=discord.Color.gold())
        await interaction.channel.send(embed=embed)

        creator = interaction.guild.get_member(ticket_data[str(self.ticket_number)].get("creator_id"))
        claimer = interaction.guild.get_member(ticket_data[str(self.ticket_number)].get("claimer_id"))
        creator_text = creator.display_name if creator else "N/A"
        claimer_text = claimer.display_name if claimer else "N/A"

        await log_action(interaction.client, f"Ticket Claimed", {
            "Created By": creator_text,
            "Claimed By": claimer_text,
            "Closed By": "N/A",
            "Ticket": f"ticket-{self.ticket_number}",
            "Channel": interaction.channel.mention
        }, self.ticket_log_channel_id)
        await interaction.response.send_message("You have claimed this ticket!", ephemeral=True)

    @discord.ui.button(style=discord.ButtonStyle.red, label="Close Ticket", emoji="🔒", custom_id="close_ticket")
    async def close_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        staff_role = interaction.guild.get_role(self.staff_role_id)
        if staff_role not in interaction.user.roles and interaction.user.id != self.ticket_creator_id:
            await interaction.response.send_message("You do not have permission to close this ticket. This action is restricted to staff members or the ticket creator.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        embed = discord.Embed(
            title="Ticket Closure",
            description=f"Confirm closing ticket-{self.ticket_number}?",
            color=discord.Color.orange()
        )
        view = ConfirmCloseView(self.ticket_creator_id, self.ticket_number, interaction.channel, self.staff_role_id, self.ticket_log_channel_id)
        message = await interaction.channel.send(embed=embed, view=view)
        confirmation_buttons = ["Proceed", "Abort"]
        ticket_data[str(self.ticket_number)]["confirmation_message_id"] = str(message.id)
        ticket_data[str(self.ticket_number)]["confirmation_message_buttons"] = confirmation_buttons
        save_ticket_data(ticket_data)
        logger.debug(f"Stored confirmation buttons for ticket {self.ticket_number}: {confirmation_buttons}")

class ConfirmCloseView(discord.ui.View):
    def __init__(self, ticket_creator_id, ticket_number, channel, staff_role_id, ticket_log_channel_id):
        super().__init__(timeout=None)
        self.ticket_creator_id = ticket_creator_id
        self.ticket_number = ticket_number
        self.channel = channel
        self.staff_role_id = staff_role_id
        self.ticket_log_channel_id = ticket_log_channel_id
        self.closed_category_id = ticket_data.get(str(self.ticket_number), {}).get("closed_tickets_category_id")

    @discord.ui.button(style=discord.ButtonStyle.green, label="Proceed", custom_id="confirm_yes")
    async def confirm_yes(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)

        closed_category = interaction.guild.get_channel(self.closed_category_id) if self.closed_category_id else None

        overwrites = {
            interaction.guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True)
        }

        try:
            if closed_category:
                await self.channel.edit(
                    overwrites=overwrites,
                    name=f"closed-ticket-{self.ticket_number}",
                    category=closed_category
                )
            else:
                await self.channel.edit(
                    overwrites=overwrites,
                    name=f"closed-ticket-{self.ticket_number}"
                )
        except discord.errors.HTTPException as e:
            logger.error(f"Failed to close ticket: {str(e)}")
            await interaction.followup.send("Failed to close the ticket due to an error.", ephemeral=True)
            return

        try:
            transcript = await generate_transcript(self.channel, self.ticket_number)
            logger.debug(f"Generated transcript for ticket {self.ticket_number}: {transcript}")
        except Exception as e:
            logger.error(f"Failed to generate transcript: {str(e)}")
            await interaction.followup.send("Failed to generate transcript. Ticket closed but transcript unavailable.", ephemeral=True)
            transcript = {"messages": [], "stats": {"opened_at": "N/A", "closed_at": "N/A", "creator": None, "closer": None, "message_count": 0, "embed_count": 0, "component_count": 0, "server_name": self.channel.guild.name}}

        token = secrets.token_hex(16)
        transcripts[self.ticket_number] = transcript
        tokens[self.ticket_number] = {"token": token, "creator_id": self.ticket_creator_id}

        # TODO: Replace with your own server address and port
        base_url = "http://YOUR_SERVER_ADDRESS:YOUR_PORT/"
        transcript_url = f"{base_url}/transcript/{self.ticket_number}?token={token}"

        ticket_data[str(self.ticket_number)]["closer_id"] = interaction.user.id
        save_ticket_data(ticket_data)

        creator = interaction.guild.get_member(ticket_data[str(self.ticket_number)].get("creator_id"))
        claimer = interaction.guild.get_member(ticket_data[str(self.ticket_number)].get("claimer_id"))
        closer = interaction.guild.get_member(ticket_data[str(self.ticket_number)].get("closer_id"))
        creator_text = creator.display_name if creator else "N/A"
        claimer_text = claimer.display_name if claimer else "N/A"
        closer_text = closer.display_name if closer else "N/A"

        await log_action(interaction.client, f"Ticket Closed", {
            "Created By": creator_text,
            "Claimed By": claimer_text,
            "Closed By": closer_text,
            "Ticket": f"ticket-{self.ticket_number}",
            "Channel": self.channel.mention
        }, self.ticket_log_channel_id, url=transcript_url)

        creator_member = interaction.guild.get_member(self.ticket_creator_id)
        if creator_member:
            embed = discord.Embed(
                title="Ticket Closed",
                description=f"Your ticket `ticket-{self.ticket_number}` has been closed by {interaction.user.display_name}.\n📜 [View Transcript]({transcript_url})",
                color=discord.Color.red(),
                timestamp=discord.utils.utcnow()
            )
            try:
                await creator_member.send(embed=embed)
            except discord.Forbidden:
                logger.warning(f"Could not DM {creator_member.display_name} (ID: {creator_member.id}) about ticket closure. DMs may be closed or bot lacks permission.")
                await self.channel.send(f"Could not DM {creator_member.mention} the transcript. Please ensure your DMs are open.")
            except Exception as e:
                logger.error(f"Error sending DM to {creator_member.display_name}: {str(e)}")
                await self.channel.send(f"Error sending transcript to {creator_member.mention}: {str(e)}")

        await self.channel.send(f"Ticket `ticket-{self.ticket_number}` has been closed by {interaction.user.mention}.")

        await interaction.followup.send("Ticket closed!", ephemeral=True)

    @discord.ui.button(style=discord.ButtonStyle.red, label="Abort", custom_id="confirm_no")
    async def confirm_no(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Ticket closure canceled.", ephemeral=True)

@app.route('/transcript/<ticket_number>')
def show_transcript(ticket_number):
    token = request.args.get('token')
    logger.debug(f"Accessing transcript for ticket {ticket_number} with token {token}")
    if not token or int(ticket_number) not in tokens or tokens[int(ticket_number)]["token"] != token:
        logger.error(f"Invalid or missing token for ticket {ticket_number}")
        abort(403)

    creator_id = tokens[int(ticket_number)]["creator_id"]
    transcript = transcripts.get(int(ticket_number))
    if not transcript:
        logger.error(f"Transcript not found for ticket {ticket_number}")
        abort(404)

    logger.debug(f"Rendering transcript for ticket {ticket_number}: {transcript}")
    html = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Ticket {{ ticket_number }} Transcript</title>
        <link rel="icon" href="https://i.imgur.com/FgynQXW.png" type="image/png">
        <style>
            body {
                background-color: #202225;
                color: #ffffff;
                font-family: Arial, sans-serif;
                margin: 20px;
            }
            .container {
                display: flex;
                flex-direction: column;
                gap: 20px;
            }
            .messages {
                background-color: #2c2f33;
                padding: 10px;
                border-radius: 5px;
            }
            .message {
                display: flex;
                align-items: flex-start;
                margin-bottom: 15px;
                padding: 10px;
                border-left: 4px solid #faa61a;
            }
            .avatar {
                width: 40px;
                height: 40px;
                border-radius: 50%;
                margin-right: 10px;
            }
            .message-content {
                flex: 1;
            }
            .display-name {
                font-weight: bold;
            }
            .timestamp {
                font-size: 12px;
                color: #72767d;
                margin-left: 5px;
            }
            .content {
                font-size: 14px;
                color: #ffffff;
                word-wrap: break-word;
                margin-top: 2px;
            }
            .embed {
                margin-top: 10px;
                padding: 10px;
                border-left: 4px solid #43B581;
                background-color: #23272a;
            }
            .embed hr {
                border: 0;
                border-top: 1px solid #72767d;
                margin: 5px 0;
            }
            .embed ul {
                padding-left: 20px;
                margin: 5px 0;
            }
            .buttons {
                margin-top: 10px;
                display: flex;
                gap: 10px;
                background-color: #1e2124;
                padding: 5px;
                border-radius: 5px;
            }
            .button {
                padding: 8px 12px;
                color: #ffffff;
                border-radius: 5px;
                font-size: 14px;
                border: 1px solid #ffffff;
                display: inline-block;
            }
            .button.claim-ticket {
                background-color: #43B581;
            }
            .button.close-ticket, .button.close {
                background-color: #ED4245;
            }
            .button.cancel {
                background-color: #72767D;
            }
            .stats {
                background-color: #23272a;
                border: 2px solid #faa61a;
                padding: 15px;
                border-radius: 5px;
            }
            .stats .key { color: #72767d; }
            .stats .value { color: #ffffff; }
        </style>
    </head>
    <body>
        <div class="container">
            <div class="messages">
                {% if transcript.messages %}
                    {% for message in transcript.messages %}
                    <div class="message">
                        <img src="{{ message.avatar_url }}" alt="Avatar" class="avatar">
                        <div class="message-content">
                            <span class="display-name" style="color: {{ message.role_color }}">{{ message.display_name }}</span>
                            <span class="timestamp">[{{ message.timestamp }}]</span>
                            {% if message.content %}
                                <div class="content">{{ message.content }}</div>
                            {% endif %}
                            {% if message.embeds %}
                                {% for embed_text, embed_color in message.embeds %}
                                    <div class="embed">
                                        <hr>
                                        {{ embed_text | safe }}
                                        <hr>
                                    </div>
                                {% endfor %}
                            {% endif %}
                            {% if message.buttons %}
                                <div class="buttons">
                                    {% for button in message.buttons %}
                                        <span class="button
                                            {% if 'Claim Ticket' in button %}claim-ticket{% endif %}
                                            {% if 'Close Ticket' in button %}close-ticket{% endif %}
                                            {% if button == 'Proceed' %}close{% endif %}
                                            {% if button == 'Abort' %}cancel{% endif %}
                                        ">{{ button | safe }}</span>
                                    {% endfor %}
                                </div>
                            {% endif %}
                        </div>
                    </div>
                    {% endfor %}
                {% else %}
                    <p>No messages found in this transcript.</p>
                {% endif %}
            </div>
            <div class="stats">
                <strong>Stats:</strong><br>
                <span class="key">Ticket Opened:</span> <span class="value">{{ transcript.stats.opened_at }}</span><br>
                <span class="key">Ticket Closed:</span> <span class="value">{{ transcript.stats.closed_at }}</span><br>
                <span class="key">Creator:</span> <span class="value">{% if transcript.stats.creator %}{{ transcript.stats.creator|member_display_name }}{% else %}N/A{% endif %}</span><br>
                <span class="key">Closed by:</span> <span class="value">{% if transcript.stats.closer %}{{ transcript.stats.closer|member_display_name }}{% else %}N/A{% endif %}</span><br>
                <span class="key">Messages:</span> <span class="value">{{ transcript.stats.message_count }}</span><br>
                <span class="key">Embeds:</span> <span class="value">{{ transcript.stats.embed_count }}</span><br>
                <span class="key">Components:</span> <span class="value">{{ transcript.stats.component_count }}</span><br>
                <span class="key">Server:</span> <span class="value">{{ transcript.stats.server_name }}</span>
            </div>
        </div>
    </body>
    </html>
    """
    def member_display_name(member_id):
        member = discord.utils.get(client.get_all_members(), id=member_id)
        return member.display_name if member else "Unknown"
    app.jinja_env.filters['member_display_name'] = member_display_name
    return render_template_string(html, ticket_number=ticket_number, transcript=transcript)

@client.tree.command(name="support", description="Open the support panel to create a ticket")
@app_commands.describe(
    panel="Select the channel where you want the panel to be sent in!",
    staff="Choose a staff role to be pinged when a new ticket is created and given access to it.",
    tickets_category="Choose where new tickets will be created.",
    closed_tickets="The category where closed tickets will be moved to.",
    logs="The channel where you will get all the ticket logs.",
    color="The color for the embed (hex code, e.g., 0xFF0000 for red, optional)",
    image="Optional URL to an image or GIF for the support panel (e.g., https://example.com/image.png)"
)
async def support(interaction: discord.Interaction, panel: discord.TextChannel, staff: discord.Role, tickets_category: discord.CategoryChannel = None, closed_tickets: discord.CategoryChannel = None, logs: discord.TextChannel = None, color: int = None, image: str = None):
    # TODO: Replace YOUR_ADMIN_USER_ID with the Discord user ID of the admin who can run this command
    if interaction.user.id != YOUR_ADMIN_USER_ID:
        await interaction.response.send_message("You do not have permission to use this command!", ephemeral=True)
        return

    server_name = interaction.guild.name
    embed = discord.Embed(
        title=f"{server_name} Support System",
        description="Welcome to our assistance center!\nTap the button below to initiate a ticket, where our expert team will promptly address your concerns.\n**Guidance:** Please remain polite, provide detailed information, and refrain from excessive pings—our support will reach out soon!",
        color=color if color is not None else 0x00FFFF
    )
    if image and (image.startswith("http://") or image.startswith("https://")) and any(image.lower().endswith(ext) for ext in ['.png', '.jpg', '.jpeg', '.gif']):
        embed.set_image(url=image)
    embed.set_footer(text="🎫 Support")
    ticket_category_id = tickets_category.id if tickets_category else None
    ticket_log_channel_id = logs.id if logs else None
    closed_category_id = closed_tickets.id if closed_tickets else None

    support_panel_data[str(interaction.guild.id)] = {
        "panel_channel_id": panel.id,
        "staff_role_id": staff.id,
        "ticket_category_id": ticket_category_id,
        "closed_tickets_category_id": closed_category_id,
        "ticket_log_channel_id": ticket_log_channel_id,
        "embed_title": f"{server_name} Support System",
        "embed_description": "Welcome to our assistance center!\nTap the button below to initiate a ticket, where our expert team will promptly address your concerns.\n**Guidance:** Please remain polite, provide detailed information, and refrain from excessive pings—our support will reach out soon!",
        "embed_color": color if color is not None else 0x00FFFF,
        "button_label": "Create Support Ticket",
        "image": image if image and (image.startswith("http://") or image.startswith("https://")) and any(image.lower().endswith(ext) for ext in ['.png', '.jpg', '.jpeg', '.gif']) else None
    }
    save_support_panel(support_panel_data)

    for ticket in ticket_data.values():
        ticket["closed_tickets_category_id"] = closed_category_id
        ticket["ticket_log_channel_id"] = ticket_log_channel_id
    save_ticket_data(ticket_data)

    view = SupportView(staff.id, ticket_category_id, ticket_log_channel_id)
    await panel.send(embed=embed, view=view)
    combined_guide = (
        "**Embed Customization Guide:**\n"
        "**Image URL Rules:**\n"
        "- Use a valid URL starting with `http://` or `https://`.\n"
        "- The URL must end with `.png`, `.jpg`, `.jpeg`, or `.gif`.\n"
        "- Avoid adding parameters like `?` or `#` at the end of the URL (e.g., use `https://example.com/image.png` instead of `https://example.com/image.png?param=value`).\n"
        "**Note on Embed Color:** If you'd like to change the color in the future using the `/edit` command, use a hex color code in the format `0xRRGGBB` (e.g., `0xFF0000` for red).\n"
        "You can pick a color and get its hex code from a site like https://www.color-hex.com/.\n"
        "Just replace the '#' with '0x' when entering the code (e.g., #FF0000 becomes 0xFF0000)."
    )
    await interaction.response.send_message(f"Support panel set up successfully!\n\n{combined_guide}", ephemeral=True)

@client.tree.command(name="edit", description="Edit the support panel (admin only)")
@app_commands.describe(
    panel_channel="The channel where the support panel is located (required)",
    title="The new title for the support panel embed (optional)",
    description="The new description for the support panel embed (optional)",
    color="The new color for the embed (hex code, e.g., 0xFF0000 for red, optional)",
    button_label="The new label for the Create Support Ticket button (optional)",
    image="Optional new URL to an image or GIF for the support panel (e.g., https://example.com/image.png, optional; leave blank to remove)"
)
async def edit_support(interaction: discord.Interaction, panel_channel: discord.TextChannel, title: str = None, description: str = None, color: int = None, button_label: str = None, image: str = None):
    # TODO: Replace YOUR_ADMIN_USER_ID with the Discord user ID of the admin who can run this command
    if interaction.user.id != YOUR_ADMIN_USER_ID:
        await interaction.response.send_message("You do not have permission to use this command!", ephemeral=True)
        return

    if not any([title, description, color is not None, button_label, image is not None]):
        embed = discord.Embed(
            title="Error",
            description="You must provide at least one option to edit (title, description, color, button_label, or image).",
            color=discord.Color.red()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    guild_id = str(interaction.guild.id)
    panel_data = support_panel_data.get(guild_id)
    if not panel_data or panel_data.get("panel_channel_id") != panel_channel.id:
        await interaction.response.send_message("No support panel found in the specified channel. Please set up a panel using /support first.", ephemeral=True)
        return

    async for message in panel_channel.history(limit=100):
        if message.author == client.user and message.embeds and message.embeds[0].footer.text == "🎫 Support":
            new_title = title if title else panel_data["embed_title"]
            new_description = description if description else panel_data["embed_description"]
            new_color = color if color is not None else panel_data["embed_color"]
            new_button_label = button_label if button_label else panel_data["button_label"]
            new_image = image if image else panel_data.get("image")

            if image and not (image.startswith("http://") or image.startswith("https://")) or (image and not any(image.lower().endswith(ext) for ext in ['.png', '.jpg', '.jpeg', '.gif'])):
                new_image = panel_data.get("image")
            elif image == "":
                new_image = None

            new_embed = discord.Embed(
                title=new_title,
                description=new_description,
                color=new_color
            )
            if new_image and (new_image.startswith("http://") or new_image.startswith("https://")) and any(new_image.lower().endswith(ext) for ext in ['.png', '.jpg', '.jpeg', '.gif']):
                new_embed.set_image(url=new_image)
            new_embed.set_footer(text="🎫 Support")

            new_view = SupportView(
                panel_data["staff_role_id"],
                panel_data["ticket_category_id"],
                panel_data["ticket_log_channel_id"],
                new_button_label
            )

            await message.edit(embed=new_embed, view=new_view)

            panel_data["embed_title"] = new_title
            panel_data["embed_description"] = new_description
            panel_data["embed_color"] = new_color
            panel_data["button_label"] = new_button_label
            panel_data["image"] = new_image
            support_panel_data[guild_id] = panel_data
            save_support_panel(support_panel_data)

            combined_guide = (
                "**Embed Customization Guide:**\n"
                "**Image URL Rules:**\n"
                "- Use a valid URL starting with `http://` or `https://`.\n"
                "- The URL must end with `.png`, `.jpg`, `.jpeg`, or `.gif`.\n"
                "- Avoid adding parameters like `?` or `#` at the end of the URL (e.g., use `https://example.com/image.png` instead of `https://example.com/image.png?param=value`).\n"
                "**Note on Embed Color:** If you'd like to change the color in the future using the `/edit` command, use a hex color code in the format `0xRRGGBB` (e.g., `0xFF0000` for red).\n"
                "You can pick a color and get its hex code from a site like https://www.color-hex.com/.\n"
                "Just replace the '#' with '0x' when entering the code (e.g., #FF0000 becomes 0xFF0000)."
            )
            await interaction.response.send_message(f"Support panel updated successfully!\n\n{combined_guide}", ephemeral=True)
            return

    await interaction.response.send_message("Could not find the support panel message in the specified channel. Please ensure the panel exists and hasn't been deleted.", ephemeral=True)

@client.tree.command(name="delete", description="Delete a ticket (staff only)")
@app_commands.describe(ticket="The ticket channel to delete")
async def delete_ticket(interaction: discord.Interaction, ticket: discord.TextChannel):
    ticket_number = ticket.name.split("-")[-1]
    ticket_info = ticket_data.get(str(ticket_number), {})
    if not ticket.name.startswith(("ticket-", "closed-ticket-")):
        await interaction.response.send_message("This channel is not a ticket channel.", ephemeral=True)
        return
    staff_role = interaction.guild.get_role(ticket_info.get("staff_role_id"))
    if not staff_role or staff_role not in interaction.user.roles:
        await interaction.response.send_message("You do not have permission to use this command. This action is restricted to staff members only.", ephemeral=True)
        return

    ticket_name = ticket.name

    await log_action(interaction.client, f"Ticket Deleted", {
        "Deleted By": interaction.user.display_name,
        "Ticket": ticket_name
    }, ticket_info.get("ticket_log_channel_id"))

    if str(ticket_number) in ticket_data:
        del ticket_data[str(ticket_number)]
        save_ticket_data(ticket_data)

    await ticket.delete()

    await interaction.response.send_message(f"Ticket {ticket_name} has been deleted.", ephemeral=True)

@client.tree.command(name="reopen", description="Reopen a closed ticket (staff only)")
@app_commands.describe(ticket="The closed ticket channel to reopen")
async def reopen_ticket(interaction: discord.Interaction, ticket: discord.TextChannel):
    ticket_number = ticket.name.split("-")[-1]
    ticket_info = ticket_data.get(str(ticket_number), {})
    staff_role = interaction.guild.get_role(ticket_info.get("staff_role_id"))
    if not staff_role or staff_role not in interaction.user.roles:
        await interaction.response.send_message("You do not have permission to use this command. This action is restricted to staff members only.", ephemeral=True)
        return
    if not ticket.name.startswith("closed-ticket-"):
        await interaction.response.send_message("This channel is not a closed ticket.", ephemeral=True)
        return

    ticket_creator_id = ticket_info.get("creator_id")
    if not ticket_creator_id:
        await interaction.response.send_message("Could not determine the ticket creator. The ticket data may be missing.", ephemeral=True)
        return

    creator = interaction.guild.get_member(ticket_creator_id)
    if not creator:
        await interaction.response.send_message("The ticket creator is no longer in the server.", ephemeral=True)
        return

    ticket_category_id = ticket_info.get("ticket_category_id")
    ticket_category = interaction.guild.get_channel(ticket_category_id) if ticket_category_id else None
    if not ticket_category and ticket_category_id:
        await interaction.response.send_message("Error: Ticket category not found!", ephemeral=True)
        return

    overwrites = {
        interaction.guild.default_role: discord.PermissionOverwrite(view_channel=False),
        creator: discord.PermissionOverwrite(view_channel=True, send_messages=True),
        staff_role: discord.PermissionOverwrite(view_channel=True, send_messages=True),
        interaction.guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True)
    }
    try:
        await ticket.edit(
            name=f"ticket-{ticket_number}",
            overwrites=overwrites,
            category=ticket_category
        )
    except discord.errors.HTTPException as e:
        logger.error(f"Failed to reopen ticket: {str(e)}")
        await interaction.response.send_message("Failed to reopen the ticket due to an error.", ephemeral=True)
        return

    creator = interaction.guild.get_member(ticket_data[str(ticket_number)].get("creator_id"))
    claimer = interaction.guild.get_member(ticket_data[str(ticket_number)].get("claimer_id"))
    closer = interaction.guild.get_member(ticket_data[str(ticket_number)].get("closer_id"))
    creator_text = creator.display_name if creator else "N/A"
    claimer_text = claimer.display_name if claimer else "N/A"
    closer_text = closer.display_name if closer else "N/A"

    await log_action(interaction.client, f"Ticket Reopened", {
        "Created By": creator_text,
        "Claimed By": claimer_text,
        "Closed By": closer_text,
        "Reopened By": f"{interaction.user.display_name}",
        "Ticket": f"ticket-{ticket_number}",
        "Channel": ticket.mention
    }, ticket_info.get("ticket_log_channel_id"))
    await interaction.response.send_message(f"Ticket {ticket.name} has been reopened.", ephemeral=True)

@client.tree.command(name="unclaim", description="Unclaim a ticket (only the claimer can use this)")
async def unclaim_ticket(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)

    if not interaction.channel.name.startswith("ticket-"):
        await interaction.followup.send("This command can only be used in a ticket channel.", ephemeral=True)
        return

    ticket_number = interaction.channel.name.split("-")[-1]
    ticket_info = ticket_data.get(str(ticket_number), {})
    claimer_id = ticket_info.get("claimer_id")

    if not claimer_id:
        await interaction.followup.send("This ticket has not been claimed.", ephemeral=True)
        return

    if claimer_id != interaction.user.id:
        await interaction.followup.send("Only the person who claimed this ticket can unclaim it.", ephemeral=True)
        return

    staff_role = interaction.guild.get_role(ticket_info.get("staff_role_id"))
    overwrites = interaction.channel.overwrites
    for member in interaction.guild.members:
        if staff_role in member.roles:
            overwrites[member] = discord.PermissionOverwrite(view_channel=True, send_messages=True)
    await interaction.channel.edit(overwrites=overwrites)

    ticket_data[str(ticket_number)].pop("claimer_id", None)
    save_ticket_data(ticket_data)

    creator = interaction.guild.get_member(ticket_data[str(ticket_number)].get("creator_id"))
    closer = interaction.guild.get_member(ticket_data[str(ticket_number)].get("closer_id"))
    creator_text = creator.display_name if creator else "N/A"
    closer_text = closer.display_name if closer else "N/A"

    await log_action(interaction.client, f"Ticket Unclaimed", {
        "Created By": creator_text,
        "Claimed By": "N/A",
        "Closed By": closer_text,
        "Unclaimed By": f"{interaction.user.display_name}",
        "Ticket": f"ticket-{ticket_number}",
        "Channel": interaction.channel.mention
    }, ticket_info.get("ticket_log_channel_id"))

    embed = discord.Embed(
        description=f"This ticket has been unclaimed by {interaction.user.display_name}.",
        color=discord.Color.blue()
    )
    await interaction.channel.send(embed=embed)
    await interaction.followup.send("You have unclaimed this ticket.", ephemeral=True)

@client.tree.command(name="claim", description="Claim a ticket (staff only)")
async def claim_ticket(interaction: discord.Interaction):
    if not interaction.channel.name.startswith("ticket-"):
        await interaction.response.send_message("This command can only be used in a ticket channel.", ephemeral=True)
        return

    ticket_number = interaction.channel.name.split("-")[-1]
    ticket_info = ticket_data.get(str(ticket_number), {})
    staff_role = interaction.guild.get_role(ticket_info.get("staff_role_id"))
    if not staff_role or staff_role not in interaction.user.roles:
        await interaction.response.send_message("You do not have permission to claim this ticket. This action is restricted to staff members only.", ephemeral=True)
        return

    if "claimer_id" in ticket_info:
        claimer = interaction.guild.get_member(ticket_info["claimer_id"])
        embed = discord.Embed(
            description=f"This ticket has already been claimed by {claimer.display_name}.",
            color=discord.Color.red()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    ticket_data[str(ticket_number)]["claimer_id"] = interaction.user.id
    save_ticket_data(ticket_data)

    overwrites = interaction.channel.overwrites
    for member in interaction.guild.members:
        if staff_role in member.roles and member.id != interaction.user.id:
            if not any(role.permissions.administrator for role in member.roles):
                overwrites[member] = discord.PermissionOverwrite(view_channel=False)
    overwrites[interaction.user] = discord.PermissionOverwrite(view_channel=True, send_messages=True)
    creator_id = ticket_data[str(ticket_number)].get("creator_id")
    if creator_id:
        overwrites[interaction.guild.get_member(creator_id)] = discord.PermissionOverwrite(view_channel=True, send_messages=True)
    await interaction.channel.edit(overwrites=overwrites)

    embed = discord.Embed(description=f"This ticket has been claimed by {interaction.user.display_name}.", color=discord.Color.gold())
    await interaction.channel.send(embed=embed)

    creator = interaction.guild.get_member(ticket_data[str(ticket_number)].get("creator_id"))
    claimer = interaction.guild.get_member(ticket_data[str(ticket_number)].get("claimer_id"))
    creator_text = creator.display_name if creator else "N/A"
    claimer_text = claimer.display_name if claimer else "N/A"

    await log_action(interaction.client, f"Ticket Claimed", {
        "Created By": creator_text,
        "Claimed By": claimer_text,
        "Closed By": "N/A",
        "Ticket": f"ticket-{ticket_number}",
        "Channel": interaction.channel.mention
    }, ticket_info.get("ticket_log_channel_id"))
    await interaction.response.send_message("You have claimed this ticket!", ephemeral=True)

@client.tree.command(name="close", description="Close a ticket (staff or ticket creator only)")
@app_commands.describe(ticket="The ticket channel to close")
async def close_ticket(interaction: discord.Interaction, ticket: discord.TextChannel):
    if not ticket.name.startswith("ticket-"):
        await interaction.response.send_message("This channel is not an open ticket.", ephemeral=True)
        return

    ticket_number = ticket.name.split("-")[-1]
    ticket_info = ticket_data.get(ticket_number, {})
    ticket_creator_id = ticket_info.get("creator_id")

    if not ticket_creator_id:
        await interaction.response.send_message("Could not determine the ticket creator. The ticket data may be missing.", ephemeral=True)
        return

    staff_role = interaction.guild.get_role(ticket_info.get("staff_role_id"))
    if not staff_role or (staff_role not in interaction.user.roles and interaction.user.id != ticket_creator_id):
        await interaction.response.send_message("You do not have permission to close this ticket. This action is restricted to staff members or the ticket creator.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    embed = discord.Embed(
        title="Ticket Closure",
        description=f"Confirm closing ticket-{ticket_number}?",
        color=discord.Color.orange()
    )
    view = ConfirmCloseView(ticket_creator_id, ticket_number, ticket, ticket_info.get("staff_role_id"), ticket_info.get("ticket_log_channel_id"))
    message = await ticket.send(embed=embed, view=view)
    confirmation_buttons = ["Proceed", "Abort"]
    ticket_data[ticket_number]["confirmation_message_id"] = str(message.id)
    ticket_data[ticket_number]["confirmation_message_buttons"] = confirmation_buttons
    save_ticket_data(ticket_data)
    logger.debug(f"Stored confirmation buttons for ticket {ticket_number}: {confirmation_buttons}")

@client.tree.command(name="add", description="Add a user or role to the ticket (staff only)")
@app_commands.describe(user="The user to add to the ticket", role="The role to add to the ticket")
async def add_to_ticket(interaction: discord.Interaction, user: discord.Member = None, role: discord.Role = None):
    if not interaction.channel.name.startswith(("ticket-", "closed-ticket-")):
        await interaction.response.send_message("This command can only be used in a ticket channel.", ephemeral=True)
        return

    ticket_number = interaction.channel.name.split("-")[-1]
    ticket_info = ticket_data.get(str(ticket_number), {})
    staff_role = interaction.guild.get_role(ticket_info.get("staff_role_id"))
    if not staff_role or staff_role not in interaction.user.roles:
        await interaction.response.send_message("You do not have permission to use this command. This action is restricted to staff members only.", ephemeral=True)
        return

    if not user and not role:
        embed = discord.Embed(
            title="Error",
            description="You must provide at least one option (user or role) to proceed. Please make a ticket in https://dsc.gg/aio-cafe if you need any help :x:",
            color=0xFF6666
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    overwrites = interaction.channel.overwrites
    if user:
        overwrites[user] = discord.PermissionOverwrite(view_channel=True, send_messages=True)
        await interaction.channel.send(f"{interaction.user.mention} has added {user.mention} to the ticket!")
        await log_action(interaction.client, "User Added to Ticket", {
            "Added By": interaction.user.display_name,
            "User": user.display_name,
            "Ticket": f"ticket-{ticket_number}",
            "Channel": interaction.channel.mention
        }, ticket_info.get("ticket_log_channel_id"))
    if role:
        overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)
        await interaction.channel.send(f"{interaction.user.mention} has added {role.mention} to the ticket!")
        await log_action(interaction.client, "Role Added to Ticket", {
            "Added By": interaction.user.display_name,
            "Role": role.name,
            "Ticket": f"ticket-{ticket_number}",
            "Channel": interaction.channel.mention
        }, ticket_info.get("ticket_log_channel_id"))
    await interaction.channel.edit(overwrites=overwrites)
    await interaction.response.send_message(f"{'User' if user else ''}{' and role' if user and role else 'Role' if role else ''} added to the ticket!", ephemeral=True)

@client.tree.command(name="remove", description="Remove a user or role from the ticket (staff only)")
@app_commands.describe(user="The user to remove from the ticket", role="The role to remove from the ticket")
async def remove_from_ticket(interaction: discord.Interaction, user: discord.Member = None, role: discord.Role = None):
    if not interaction.channel.name.startswith(("ticket-", "closed-ticket-")):
        await interaction.response.send_message("This command can only be used in a ticket channel.", ephemeral=True)
        return

    ticket_number = interaction.channel.name.split("-")[-1]
    ticket_info = ticket_data.get(str(ticket_number), {})
    staff_role = interaction.guild.get_role(ticket_info.get("staff_role_id"))
    if not staff_role or staff_role not in interaction.user.roles:
        await interaction.response.send_message("You do not have permission to use this command. This action is restricted to staff members only.", ephemeral=True)
        return

    if not user and not role:
        embed = discord.Embed(
            title="Error",
            description="You must provide at least one option (user or role) to proceed. Please make a ticket in https://dsc.gg/aio-cafe if you need any help :x:",
            color=0xFF6666
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    overwrites = interaction.channel.overwrites
    if user:
        if user.id == interaction.user.id and staff_role in user.roles:
            await interaction.response.send_message("You cannot remove yourself from the ticket while retaining staff access.", ephemeral=True)
            return
        overwrites.pop(user, None)
        await interaction.channel.send(f"{interaction.user.mention} has removed {user.mention} from the ticket!")
        await log_action(interaction.client, "User Removed from Ticket", {
            "Removed By": interaction.user.display_name,
            "User": user.display_name,
            "Ticket": f"ticket-{ticket_number}",
            "Channel": interaction.channel.mention
        }, ticket_info.get("ticket_log_channel_id"))
    if role:
        if role.id == staff_role.id:
            for member in interaction.guild.members:
                if member.id != interaction.user.id and staff_role in member.roles:
                    overwrites.pop(member, None)
            overwrites[interaction.user] = discord.PermissionOverwrite(view_channel=True, send_messages=True)
        else:
            overwrites.pop(role, None)
        await interaction.channel.send(f"{interaction.user.mention} has removed {role.mention} from the ticket!")
        await log_action(interaction.client, "Role Removed from Ticket", {
            "Removed By": interaction.user.display_name,
            "Role": role.name,
            "Ticket": f"ticket-{ticket_number}",
            "Channel": interaction.channel.mention
        }, ticket_info.get("ticket_log_channel_id"))
    await interaction.channel.edit(overwrites=overwrites)
    await interaction.response.send_message(f"{'User' if user else ''}{' and role' if user and role else 'Role' if role else ''} removed from the ticket!", ephemeral=True)

async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    error_message = str(error)
    embed = discord.Embed(
        title="Error",
        description=f"{error_message}. Please make a ticket in https://dsc.gg/aio-cafe if you need any help :x:",
        color=0xFF6666
    )
    try:
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)
    except Exception as e:
        logger.error(f"Failed to send error message: {str(e)}")

client.tree.on_error = on_app_command_error

@client.event
async def on_message(message):
    pass

@client.event
async def on_ready():
    print(f'{client.user} has connected to Discord!')
    try:
        synced = await client.tree.sync()
        print(f"Synced {len(synced)} commands: {', '.join(cmd.name for cmd in synced)}")
        logger.info("Commands synced: /support, /edit, /delete, /reopen, /unclaim, /claim, /close, /add, /remove")
    except Exception as e:
        print(f"Error syncing commands: {e}")
        logger.error(f"Error syncing commands: {e}")

def run_flask():
    # TODO: Replace YOUR_PORT with the port number you want the Flask server to run on
    app.run(host='0.0.0.0', port=YOUR_PORT, debug=False)

if __name__ == "__main__":
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    # Ensure you have a token.txt file with your Discord bot token
    with open("token.txt", "r") as f:
        token = f.read().strip()
    client.run(token)