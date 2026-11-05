# Wispbyte deployment

This is a long-running Discord client, not a web server. Create a Python
worker/bot server in Wispbyte rather than a web server.

## Files and startup

Upload the application files (`bot.py`, `evaluator.py`, `stealth.py`,
`requirements.txt`, and `Procfile`) to the server. Use Python 3.12 if Wispbyte
lets you select a Python version. Install dependencies with:

```sh
python3 -m pip install -r requirements.txt
```

Set the server's startup command to:

```sh
python3 bot.py
```

The included `Procfile` declares the same command as a worker for panels that
support Procfile-based startup. Keep the process running as a worker; no port
or HTTP health check is required.

## Environment variables

Add these in Wispbyte's environment-variable settings. Do not upload `.env` or
put a live token in source control or the startup command.

| Variable | Required | Value |
|---|---|---|
| `DISCORD_USER_TOKEN` | Yes | Token for the account used by the client |
| `TARGET_CHANNELS` | Yes | Comma-separated channel IDs |
| `DROP_BOT_ID` | Yes | ID of the bot that posts drop announcements |
| `WISHLIST_EVALUATOR_ID` | No | Evaluator bot ID; defaults to the value in `.env.example` |
| `DATA_DIR` | Recommended | Writable persistent directory for state and instance lock; defaults to the application directory |
| `DISCORD_TIMEZONE` | Recommended | IANA timezone, e.g. `Europe/Berlin` |

Other optional settings and their defaults are listed in `.env.example`.
Configure only values you need; keep secrets in Wispbyte's private environment
settings.

The process writes `kd-state.json` and `.kd.pid` to `DATA_DIR`. Use a persistent
directory so restarts retain the scheduler state, and run only one copy against
the same account and data directory.

## Verify the deployment

Start the worker and check its console logs. A successful login logs
`Connected as ...`. If it exits, check the first configuration, dependency, or
login error in the logs before restarting it. The CAPTCHA handler needs console
input if a CAPTCHA is presented; if Wispbyte does not provide interactive
console input, the client cannot complete that login.

This project automates a Discord user account. Discord prohibits automating
normal user accounts, and using it may lead to account enforcement. Deploy only
if you have confirmed the relevant platform rules permit your use.
