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

Create a server using Wispbyte's Python image. See Wispbyte's
[Discord bot hosting guide](https://wispbyte.com/how-to-host-discord-bot-wispbyte)
and [startup settings guide](https://wispbyte.com/kb/startup-settings). Use
the Python 3.11 image used by this server (or another supported Python 3 image).
The current plan has 512 MB RAM, 1 GB disk, and 35% CPU; member chunking and
member caching are disabled by default to limit memory use.

1. Upload the project files, including `requirements.txt` and the `wheels/`
   directory, in the file manager, or deploy the repository through Wispbyte's
   GitHub integration. Set `REQUIREMENTS_FILE` to `requirements.txt` and leave
   **User Uploaded Files** set to `0` (false), or Wispbyte will skip dependency
   installation. Its startup command installs requirements on each start;
   check logs for pip errors.

   The exact `discord.py-self` wheel is included locally because the PyPI 2.1
   release does not provide the `HeadersContext` API used here. Other
   dependencies are resolved from PyPI; Wispbyte says its free tier supports
   outbound internet and package installation. If installation fails, inspect
   the startup log for the failing package. The included wheel is built from
   the pinned upstream commit and has a checksum in `wheels/SHA256SUMS`.

2. Set the **App py file** variable (`PY_FILE`) to `bot.py`; the default
   startup command points to `main.py`. The generated startup command launches
   the selected file. Keep its dependency-install clauses, but add `-u` to the
   final Python invocation so it ends with
   `/usr/local/bin/python -u /home/container/bot.py`. `Procfile` is not used by
   this Wispbyte startup template.

3. Configure `DISCORD_USER_TOKEN`, `TARGET_CHANNELS`, and `DROP_BOT_ID` using
   a private `.env` file uploaded through Wispbyte's file manager; the listed
   startup variables are not the app's Discord configuration. Matching
   non-empty process environment values override `.env`.

   Also configure these optional values as needed:

   | Variable | Required | Description |
   |---|---|---|
   | `WISHLIST_EVALUATOR_ID` | No | Evaluator ID; defaults to `1380936713639166082` |
   | `DATA_DIR` | Recommended | Writable persistent directory for state and lock files |
   | `DISCORD_TIMEZONE` | Recommended | Your own IANA timezone; set it explicitly if the container reports UTC |
   | `CAPTCHA_KEY` | No | Operator-supplied CAPTCHA response; if unset, the process exits with a log message when challenged |

   Keep `.env` private and never commit it; `.gitignore` excludes it. Other
   optional settings are listed in `.env.example`. Keep **Additional Python
   Packages** empty; dependencies are installed from `requirements.txt` on
   startup. `PYTHONUNBUFFERED` in `.env` would be too late to configure the
   interpreter; the `-u` startup option enables unbuffered logs.

Use persistent storage for `DATA_DIR` so restarts retain the schedule, and run
only one copy against the same account and data directory. The bot retries a
busy instance lock a few times before exiting. No port or HTTP health check is
needed. Check the worker logs for `Connected as ...` to confirm login. A
CAPTCHA does not prompt for console input: provide a valid `CAPTCHA_KEY` through
the panel or `.env` and restart, otherwise the process exits with instructions.
Hosting network latency can make reactions slower than a local run.
Wispbyte documents shared-IP Discord login limits; see its
[Discord rate-limits guide](https://wispbyte.com/kb/discord-rate-limits) if the
logs show repeated 429 or Cloudflare 1015 responses.

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
