# telegram-multichannel-buttons-bot (v0.4)

Telegram bot for publishing posts with colored URL buttons to multiple channels. Users send
the bot a post, get a preview with buttons, then publish it to a connected channel. Access is
managed per channel.

The bot's messages are in Russian.

## Features

- **Post syntax**: post text, then a `---` line, then one button row per line.
  Buttons in a row are separated by `|`:

  ```
  Post text
  ---
  !r Red - https://example.com
  !g Green - https://example.com | !b Blue - https://t.me/example
  Plain - https://example.org
  ```

  Style prefixes: `!r`/`!red` (danger), `!g`/`!green` (success), `!b`/`!blue` (primary).
  Russian aliases work too. Without a prefix the button is a regular one.
- Text posts and posts with photo, video, document or animation. Message entities
  (formatting) are preserved.
- **Draft preview** with inline controls: publish to the active channel, pick another channel,
  or cancel. Drafts expire after `PREVIEW_TTL_HOURS`.
- **Multi-channel access model**: global owners (`OWNER_IDS`), per-channel managers and users.
  Optionally, channel admins can connect their own channels.
- SQLite storage for channels, users, permissions and drafts.
- Uses the Telegram Bot API directly via long polling (`requests` only).

## Commands

| Command | Action |
|---|---|
| `/start` | Help and post syntax |
| `/addchannel @channel` | Connect a channel (the bot must be a channel admin) |
| `/channels` | My channels |
| `/usechannel 1` | Select the active channel |
| `/channel` | Show the current channel |
| `/adduser @channel <id>` / `/removeuser @channel <id>` | Grant/revoke access |
| `/makeadmin @channel <id>` / `/unmakeadmin @channel <id>` | Grant/revoke manager role |
| `/users @channel` | List channel users |
| `/removechannel @channel` | Disconnect a channel |
| `/refreshchannel <channel>` | Refresh channel info |
| `/myid` | Show your Telegram ID |

## Tech Stack

- Python 3
- requests
- SQLite (`sqlite3`)

## Installation

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env    # then edit .env
python bot.py
```

## Configuration

`.env` is read from the project directory and from the current directory. Real environment
variables take priority.

| Variable | Default | Description |
|---|---|---|
| `BOT_TOKEN` | (required) | Bot token from @BotFather |
| `OWNER_IDS` | empty | Global owners, comma-separated |
| `ALLOW_SELF_ADD_CHANNELS` | `true` | Allow channel admins to connect channels themselves |
| `DATABASE_PATH` | `data/bot.sqlite3` | SQLite database path |
| `PREVIEW_TTL_HOURS` | `24` | Draft lifetime |
| `LOG_LEVEL` | `INFO` | Logging level |
| `API_TIMEOUT`, `POLL_TIMEOUT` | `35`, `25` | HTTP and long-poll timeouts, seconds |

## Project Structure

```
bot.py               entry point
tgposter/
  app.py             command and callback handling, drafts, publishing
  parser.py          post and button syntax parser
  telegram.py        Bot API client
  db.py              SQLite storage
  config.py          .env loader and settings
```

## Notes

- Colored buttons use the `style` field of inline keyboard buttons. Older Telegram clients
  may show them as regular buttons.
- The package version string is `4.0.0`; the directory name says `V0.4`.
