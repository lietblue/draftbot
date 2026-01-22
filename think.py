import os
import asyncio
import logging
import time
from telethon import TelegramClient, events
from dotenv import load_dotenv
import litellm

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logging.getLogger('telethon').setLevel(logging.WARNING)

# Configuration
API_ID = os.getenv('TELEGRAM_API_ID')
API_HASH = os.getenv('TELEGRAM_API_HASH')
SESSION_NAME = 'think_bot_session'
# Default to a capable model, but allow override
LLM_MODEL = os.getenv('LLM_MODEL', 'gpt-4o')

if not API_ID or not API_HASH:
  print("Error: TELEGRAM_API_ID and TELEGRAM_API_HASH must be set in a .env file.")
  exit(1)

async def main():
  async with TelegramClient(SESSION_NAME, int(API_ID), API_HASH) as client:
    print(f"ThinkBot Connected! Using model: {LLM_MODEL}")
    print("Listening for command: >")

    async def run_spinner(event):
      frames = ["|", "/", "-", "\\"]
      i = 0
      try:
        while True:
          await event.edit(frames[i])
          i = (i + 1) % len(frames)
          await asyncio.sleep(0.1)
      except asyncio.CancelledError:
        pass
      except Exception:
        pass

    @client.on(events.NewMessage(outgoing=True, pattern=r'^>\s*$'))
    async def think_handler(event):
      try:
        chat = await event.get_chat()
        chat_title = getattr(chat, 'title', getattr(chat, 'first_name', str(chat.id)))
        print(f"Thinking in {chat_title}...")

        # 1. Fetch Context
        history = []
        # We need to skip the > command itself, so we take limit=21 and skip the first (which is event.message)
        # Actually, iter_messages includes the current message if offset is not set?
        # event.message is the one triggering this.
        # Let's just fetch 21 and filter out the !think command if present, or just take the context.

        messages = []
        async for msg in client.iter_messages(chat, limit=21):
          messages.append(msg)

        # Reverse to chronological order
        messages.reverse()

        for msg in messages:
          # Skip the !think command itself from context if we want, or keep it.
          # Usually better to exclude the trigger command to avoid confusion,
          # but including it is fine too. Let's exclude the very last message if it is !think.
          if msg.id == event.id:
            continue

          sender = await msg.get_sender()
          name = "Unknown"
          if sender:
            name = getattr(sender, 'first_name', None) or getattr(sender, 'title', None) or str(sender.id)

          text = msg.text or "[Media/Empty]"
          history.append(f"{name}: {text}")

        context_str = "\n".join(history)

        # 2. Prompting
        prompt = f"""
You are a silly catgirl lurking in the telegram chat.
Here is the conversation context (last 20 messages):
{context_str}

Instruction:
Based on the above online chat, think of a quickwitted, clever, or funny response to the last message (or the general situation).
You can uwuspeak a bit but don't exaggerate your personality, put it mildly. you can use kaomojis freely, but emojis are strictly forbidden.
Do not include any prefixes. Just provide the raw text of the response.
"""

        # Start spinner
        spinner_task = asyncio.create_task(run_spinner(event))

        # 3. Stream Response
        generated_text = ""
        last_update_time = time.time()

        try:
          loop = asyncio.get_running_loop()
          queue: asyncio.Queue = asyncio.Queue()

          def _stream_in_thread():
            try:
              response = litellm.completion(
                model=LLM_MODEL,
                messages=[{"role": "user", "content": prompt}],
                stream=True,
              )
              for chunk in response:
                asyncio.run_coroutine_threadsafe(queue.put(chunk), loop).result()
              asyncio.run_coroutine_threadsafe(queue.put(None), loop).result()
            except Exception as e:
              asyncio.run_coroutine_threadsafe(queue.put(e), loop).result()

          producer_task = asyncio.create_task(asyncio.to_thread(_stream_in_thread))

          first_chunk = True
          while True:
            chunk = await queue.get()
            if chunk is None:
              break
            if isinstance(chunk, Exception):
              raise chunk
            content = chunk.choices[0].delta.content or ""
            if content:
              if first_chunk:
                spinner_task.cancel()
                try:
                  await spinner_task
                except asyncio.CancelledError:
                  pass
                first_chunk = False

              # Add a tiny delay for each chunk to make it look "natural" or slower
              generated_text += content
              await asyncio.sleep(0.1)

              # Throttle updates to avoid FloodWait
              current_time = time.time()
              if current_time - last_update_time > 0.5:  # Update every 0.5s
                try:
                  await event.edit(generated_text + " █")  # Cursor effect
                  last_update_time = current_time
                except Exception:
                  # Ignore edit errors (like same text)
                  pass
          await producer_task
        finally:
          if not spinner_task.done():
            spinner_task.cancel()

        # Final update without cursor
        if generated_text:
          await event.edit(generated_text)
        else:
          await event.edit("Someone tell rin there's a problem with my AI.")
        print(f"Finished thinking in {chat_title}.")

      except Exception as e:
        error_msg = f"Error: {str(e)}"
        print(error_msg)
        try:
          await event.edit(error_msg)
        except:
          pass

    await client.run_until_disconnected()

if __name__ == '__main__':
  asyncio.run(main())
