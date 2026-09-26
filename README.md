# KarutaOCR

A Discord user bot that OCRs character name screenshots and reacts to the most popular anime character.

## Setup

1. Create a `.env` file in the project root.
2. Put your Discord user account token into `DISCORD_USER_TOKEN`.
3. Add your OCR.Space API key to `OCR_API_KEY`.
4. Add a Hugging Face access token with Inference Providers access to `HF_TOKEN`. Set `HF_MODEL` to a chat model available to your account; the default is `Qwen/Qwen2.5-7B-Instruct:fastest`. DuckDuckGo search is used for web evidence and does not require an API key.
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
   HF_TOKEN=your_hugging_face_access_token
   HF_MODEL=Qwen/Qwen2.5-7B-Instruct:fastest
   DISCORD_USER_TOKEN=your_discord_user_token
   TARGET_CHANNELS=123456789012345678
   DROP_BOT_ID=your_karuta_bot_user_id
   ```

The user bot sends `kd` no more than once every 30 minutes. After sending `kd`, it waits for a matching `dropping` announcement from `DROP_BOT_ID`, including active-server messages such as `I'm dropping 4 cards since this server is currently active!`. It supports the announcement and image arriving in the same message, and ignores unrelated messages. After a successful emoji grab, it waits 10 minutes before grabbing again. It only reacts after OCR extracts names and the AniList popularity lookup completes.

OCR.Space positional data groups each horizontal card column and retains its scanned series. AniList matches must agree with both the character name and an associated media title, reducing false matches when unrelated characters share a name. When AniList cannot confidently match every card, the bot fetches DuckDuckGo web results and provides their titles and snippets to the configured Hugging Face chat model as evidence. Hugging Face Inference Providers have account- and provider-dependent availability and free usage limits; DuckDuckGo HTML search does not require a key and may be rate-limited. If a match remains uncertain, the bot does not react.

> This uses a Discord user account token rather than a bot token. Automating a user account may violate Discord's Terms of Service; use it only where permitted.
