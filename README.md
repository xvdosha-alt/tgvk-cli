EN | [RU](docs/README_RU.md)

## tgvk 🔗

CLI client: all incoming Telegram messages are mirrored to VK, controlled via short commands in VK.

## ✨ Features

- Connect via **Telegram session string** (Telethon `StringSession`)
- Forward incoming messages to VK with **who** and **where** they wrote from
- Local **history** in SQLite
- **Img mode** - new messages arrive as a chat screenshot
- Control via VK bot short commands

## 🚀 Quick start

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\\Scripts\\activate
pip install -e .
tgvk init
tgvk run
```

## ⚙️ Configuration

### Telegram

1. Create an app at [my.telegram.org/apps](https://my.telegram.org/apps) and get `api_id` / `api_hash`.
2. Set them via env or config:

```bash
export TGVK_TELEGRAM_API_ID=123456
export TGVK_TELEGRAM_API_HASH=your_api_hash
```

or:

```bash
tgvk config set telegram_api_id 123456
tgvk config set telegram_api_hash "your_api_hash"
```

3. Get session string:

```bash
python scripts/export_session.py
```

Enter phone number and code - the script prints the string.

### VK

1. Create a community → Manage → API → Create key
2. Enable permissions: **Community messages**, **Community management**
3. Enable **Community messages** in settings (Messages section)
4. `vk_peer_id` - your numeric user id (find via [@idvk_bot](https://vk.com/idvk_bot))
5. Send the bot any message to open a dialog

### Config file

File: `~/.config/tgvk/config.json`

```bash
tgvk config show
tgvk config set img_mode true
tgvk config set vk_peer_id 123456789
```

## 📋 VK bot commands

| Command | Description |
|---------|-------------|
| `ст` | Status |
| `ист` | Last 20 messages from DB |
| `ист <chat_id> 30` | 30 messages from chat |
| `чат` | Recent chat list |
| `img` | Screenshot of last messages |
| `img <chat_id> 15` | Chat screenshot |
| `img+` / `img-` | Enable/disable img mode |
| `лимит 50` | Limit for `ист` |
| `отв @ivan текст` | Reply in Telegram (reply in last chat) |
| `отв 123456789 текст` | Reply by user id |
| `лс 8973446217 привет` | DM (always private) |
| `лс @ivan привет` | DM by username |
| `игнор @ivan` / `игнор 123` | Do not forward user messages |
| `анигнор @ivan` / `анигнор 123` | Remove user from ignore |
| `игнор` | Full ignore list |
| `стоп` / `старт` | Pause / resume TG forwarding |
| `img+` / `img-` / `имг+` / `имг-` | Enable/disable img mode |
| `групп+` / `групп-` | Ignore **all** groups and supergroups |
| `канал+` / `канал-` | Ignore **all** channels |
| `игнорчат -100123` | Ignore one chat (optional) |
| `помощь` | Help |

## CLI

```bash
tgvk init          # interactive setup
tgvk run           # start bridge
tgvk run -v        # verbose logs
tgvk history -n 30 # history from DB without running bridge
tgvk config show
```

## 🎮 Forward format (text mode)

```
📩 Иван Петров (@ivan)
🆔 user:123456789 · chat:-100987654321
📍 Рабочий чат · супергруппа
--------------------
Привет, как дела?
```

## Img mode

In `img+` mode, each new message is rendered as a chat screenshot (dark theme) and sent as a photo to VK.

## Notes

- Works only with **incoming** messages (what people write to you)
- Session string is stored locally in `~/.config/tgvk/config.json` - do not share the file
- Keep `tgvk run` running for stable operation (screen/tmux/systemd)
