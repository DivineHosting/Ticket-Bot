# 🎟️ Discord Ticket Bot

A powerful and customizable Discord Ticket Bot built using **Python** and **Discord.py**.  
This bot allows server admins to easily manage support tickets through interactive commands and buttons.

---

## 🚀 Features

- Create and manage support tickets with ease  
- Admin and moderator-only ticket controls  
- Auto-assigns ticket numbers and categories  
- Customizable server address, port, and admin IDs  
- Easy setup and configuration  

---

## 🧰 Requirements

Before running the bot, make sure you have the following installed:

- Python 3.9 or higher  
- `discord.py` (listed in `requirements.txt`)  

Install dependencies using:

```bash
pip install -r requirements.txt


⚙️ Setup Guide
Clone the repository or extract the ZIP file git clone https://github.com/DivineHosting/Ticket-Bot.git
cd tickets_system

Add your bot token
Open token.txt
Paste your Discord bot token inside (no quotes or spaces)
Edit Configuration Lines Follow the Replacement Guide below to properly configure the bot.
Run the bot python main.py

Invite the bot to your server Use your bot’s OAuth2 URL with the proper permissions.

🧾 Replacement Guide
Make sure to replace the following lines inside main.py before running the bot:
Replacement Line 427 Replace:
"http://YOUR_SERVER_ADDRESS:YOUR_PORT"

With your server address and port number.

Replacement Line 667 Replace:
"Your Admin User ID"

With your own Discord User ID (Admin ID).

Replacement Line 728 Replace:
"Your Admin User ID"

With your own Discord User ID (Admin ID).

Replacement Line 1163 Replace:
"YOUR_PORT"

With your server’s port (exactly 5 digits).

🧑‍💻 Example Configuration
# Example for line 427
server_url = "http://127.0.0.1:80800"

# Example for admin ID
ADMIN_ID = 123456789012345678

# Example for port
PORT = 80800


🛠️ File Structure
tickets_system/
├── main.py              # Main bot script
├── token.txt            # Bot token file
├── requirements.txt     # Python dependencies
└── README.md            # Documentation (this file)


📜 License
This project is licensed under the MIT License — feel free to modify and use it for your own purposes.

💬 Support
If you need help configuring or running the bot, feel free to open an issue or contact the maintainer.

Made with ❤️ for the Discord community.
