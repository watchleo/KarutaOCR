# KarutaOCR

A Discord user bot that OCRs character name screenshots and reacts to the most popular anime character.

## Setup

1. Create a `.env` file in the project root.
2. Put your Discord user account token into `DISCORD_USER_TOKEN`.
3. Add your OCR.Space API key to `OCR_API_KEY`.
4. Add your Google Gemini API key to `GEMINI_API_KEY` and set `GEMINI_MODEL` to `gemini-3.1-flash-lite`, which supports the Google Search grounding used by this bot.
5. Optionally add `TARGET_CHANNELS` as a comma-separated list of channel IDs.
6. Set `DROP_BOT_ID` to the user ID of the bot that announces and posts the card drop.
7. Start the bot:

   ```bash
   npm install
   npm start
   ```

   Example `.env`:

   ```env
   OCR_API_KEY=your_ocr_space_key
   GEMINI_API_KEY=your_google_gemini_api_key
   GEMINI_MODEL=gemini-3.1-flash-lite
   DISCORD_USER_TOKEN=your_discord_user_token
   TARGET_CHANNELS=123456789012345678
   DROP_BOT_ID=your_karuta_bot_user_id
   ```

The user bot sends `kd` no more than once every 30 minutes. After sending `kd`, it waits for a matching `dropping` announcement from `DROP_BOT_ID`, including active-server messages such as `I'm dropping 4 cards since this server is currently active!`. It supports the announcement and image arriving in the same message, and ignores unrelated messages. After a successful emoji grab, it waits 10 minutes before grabbing again. It only reacts after OCR extracts names and the AniList popularity lookup completes.

OCR.Space positional data groups each horizontal card column before extracting the character name, so titles and print metadata are not mistaken for names. AniList handles recognized characters. When any OCR name is unresolved, the bot sends the full card list to Gemini with Google Search grounding and uses its popularity evaluation. If Gemini is unavailable or uncertain, the bot does not react.

> This uses a Discord user account token rather than a bot token. Automating a user account may violate Discord's Terms of Service; use it only where permitted.
