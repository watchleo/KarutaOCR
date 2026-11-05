# Karuta Wishlist Evaluator

> **Discord account and Terms of Service warning**
>
> This project connects with a Discord user-account token through
> [`discord.py-self`](https://github.com/dolfies/discord.py-self). Discord
> prohibits automating normal user accounts; use may result in account action
> or termination. A private server does not change those rules. Use this only
> if the applicable platform rules permit it.

This Python client sends `kd` in configured channels, watches for Karuta drop
announcements, and reads the configured wishlist evaluator's follow-up message.
When print numbers are present, it reacts to the card with the lowest print
number up to 999, skipping the drop if all print numbers exceed 999. If the
evaluator omits print numbers, it chooses the slot with the highest positive
wishlist count, breaking ties in favor of the lower slot number. It skips if
all counts are zero.

Evaluator lines should look like:

```text
<:yel_flower:...> <:no_1:...> `♡` `2` · **Character Name** · Series Name
```

Print-number entries such as `` `#1` · `◈2` `` may appear between the wishlist
count and character name. The `no_N` number is treated as the original drop
slot. The evaluator's Discord user ID defaults to `1380936713639166082` and
can be overridden with `WISHLIST_EVALUATOR_ID`.

The client waits a random 18–85 seconds before its first `kd` command. Later
commands are scheduled at least 30 minutes apart, with additional timing
variation. Successful grabs use a 10-minute cooldown; failed or unconfirmed
grabs use a 75-second cooldown. The command schedule and installation ID are
saved in `kd-state.json` under `DATA_DIR`.

## Deploy on Wispbyte

Create a Python worker/bot server, not a web server. Use Python 3.12 if the
panel offers a Python version selector.

1. Upload `bot.py`, `evaluator.py`, `stealth.py`, `requirements.txt`, and
   `Procfile` (or deploy the repository).
2. Install dependencies:

   ```sh
   python3 -m pip install -r requirements.txt
   ```

3. Set the startup command to:

   ```sh
   python3 bot.py
   ```

4. Configure the environment variables:

   | Variable | Required | Description |
   |---|---|---|
   | `DISCORD_USER_TOKEN` | Yes | Token for the account used by the client |
   | `TARGET_CHANNELS` | Yes | Comma-separated channel IDs |
   | `DROP_BOT_ID` | Yes | ID of the bot that posts drop announcements |
   | `WISHLIST_EVALUATOR_ID` | No | Evaluator ID; defaults to `1380936713639166082` |
   | `DATA_DIR` | Recommended | Writable persistent directory for state and lock files |
   | `DISCORD_TIMEZONE` | Recommended | IANA timezone, e.g. `Europe/Berlin` |

   Other optional settings are listed in `.env.example`. Keep the token in
   Wispbyte's private environment settings; do not put it in the startup command
   or upload a `.env` containing credentials.

Use persistent storage for `DATA_DIR` so restarts retain the schedule, and run
only one copy against the same account and data directory. No port or HTTP
health check is needed. Check the worker logs for `Connected as ...` to confirm
login. If a CAPTCHA is presented, the client asks for interactive console
input; it cannot complete CAPTCHA in a panel without an interactive console.

See [DEPLOYMENT.md](DEPLOYMENT.md) for additional deployment notes.

## Run locally

Python 3.10 or newer is required.

1. Copy `.env.example` to `.env`.
2. Set `DISCORD_USER_TOKEN`, `TARGET_CHANNELS`, and `DROP_BOT_ID`.
3. Install dependencies and start the client:

   ```sh
   python -m venv .venv
   source .venv/bin/activate
   python -m pip install -r requirements.txt
   python bot.py
   ```

   On Windows, activate the virtual environment with
   `.venv\Scripts\activate` instead.

## Testing

Run the evaluator parser tests with:

```sh
python -m unittest
```

## Credentials and security

- Never commit `.env`, account tokens, or other credentials. `.env` is ignored
  by Git; use `.env.example` as a template.
- If a credential is exposed, revoke or rotate it with the provider immediately.
- Do not share credentials in bug reports. See [SECURITY.md](SECURITY.md) for
  vulnerability reporting guidance.
