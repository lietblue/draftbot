import os
import asyncio
import argparse
import collections
from telethon import TelegramClient, events
from dotenv import load_dotenv

import logging

# Load environment variables
load_dotenv()

# Configure logging to suppress verbose connection errors
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logging.getLogger('telethon').setLevel(logging.WARNING)

# Get credentials from environment variables
API_ID = os.getenv('TELEGRAM_API_ID')
API_HASH = os.getenv('TELEGRAM_API_HASH')
SESSION_NAME = 'draft_bot_session'

# Configuration
MARKER = "\n>_"
AUTOSQUASH_ENABLED = False
# Lock to prevent race conditions per chat (e.g., fast typing or incoming + outgoing same time)
CHAT_LOCKS = collections.defaultdict(asyncio.Lock)

if not API_ID or not API_HASH:
    print("Error: TELEGRAM_API_ID and TELEGRAM_API_HASH must be set in a .env file.")
    exit(1)

def parse_arguments():
    parser = argparse.ArgumentParser(description="Telegram Draft & Squash Bot")
    parser.add_argument('-d', '--dry-run', action='store_true', help="Print actions without executing them")
    return parser.parse_args()

def is_plain_text(message):
    """Returns True if message is strictly plain text (no media, no forwards, has text)."""
    return bool(message.text and not message.media and not message.fwd_from)

async def strip_marker_from_last_message(client, chat_id):
    """Helper to find the last marked message by me in a chat and strip the marker."""
    # Search last 10 messages to find one with a marker.
    # Just checking limit=1 fails if the latest message is the sticker that triggered this boundary.
    async for msg in client.iter_messages(chat_id, from_user='me', limit=10):
        if msg.text and msg.text.endswith(MARKER):
            new_text = msg.text[:-len(MARKER)]
            try:
                await msg.edit(new_text)
                print(f"[Autosquash] Boundary hit. Removed marker from message {msg.id}.")
            except Exception as e:
                print(f"[Autosquash] Failed to strip marker: {e}")
            return # Only strip the most recent one found

async def main():
    args = parse_arguments()

    async with TelegramClient(SESSION_NAME, int(API_ID), API_HASH) as client:
        print(f"Connected! (Dry Run: {args.dry_run})")
        print("Listening for commands...")
        print("  !squash [n]      -> Merge messages")
        print("  !autosquash on/off -> Toggle auto-squashing mode")

        # --- Command: !autosquash on/off ---
        @client.on(events.NewMessage(outgoing=True, pattern=r'(?i)^!autosquash\s+(on|off)$'))
        async def toggle_autosquash(event):
            global AUTOSQUASH_ENABLED
            mode = event.pattern_match.group(1).lower()

            if mode == 'on':
                AUTOSQUASH_ENABLED = True
                print(">>> AUTOSQUASH ENABLED <<<")
                await event.edit("`Autosquash Enabled. New messages will be merged.`")
            else:
                AUTOSQUASH_ENABLED = False
                print(">>> AUTOSQUASH DISABLED <<<")
                await event.edit("`Autosquash Disabled.`")

                # Cleanup: Strip marker from last message in this chat if exists
                await strip_marker_from_last_message(client, event.chat_id)

            # Delete the status message after a few seconds to keep chat clean
            await asyncio.sleep(3)
            await event.delete()

        # --- Command: !squash ---
        @client.on(events.NewMessage(outgoing=True, pattern=r'^!squash(?:\s+(\d+))?\s*$'))
        async def squash_handler(event):
            try:
                n_str = event.pattern_match.group(1)
                chat = await event.get_chat()
                chat_name = getattr(chat, 'title', getattr(chat, 'first_name', str(chat.id)))

                messages = []

                if n_str:
                    n = int(n_str)
                    print(f"Command: !squash {n} in {chat_name}")
                    if n < 1:
                        if not args.dry_run: await event.delete()
                        return

                    async for msg in client.iter_messages(chat, from_user='me', limit=n, offset_id=event.id):
                        if not is_plain_text(msg):
                            print(f"Aborting: Message {msg.id} in fixed range {n} is not plain text.")
                            if not args.dry_run: await event.delete()
                            return
                        messages.append(msg)
                else:
                    print(f"Command: !squash (smart) in {chat_name}")
                    async for msg in client.iter_messages(chat, limit=100, offset_id=event.id):
                        if msg.out and is_plain_text(msg):
                            messages.append(msg)
                        else:
                            break

                if not messages:
                    print("No messages found to squash.")
                    if not args.dry_run: await event.delete()
                    return

                messages.reverse()

                # Cleanup markers from the messages we are about to squash
                # (in case we squash a bunch of previously autosquashed messages manually)
                cleaned_texts = []
                for m in messages:
                    txt = m.text
                    if txt.endswith(MARKER):
                        txt = txt[:-len(MARKER)]
                    cleaned_texts.append(txt)

                target_msg = messages[0]
                msgs_to_delete = messages[1:]
                combined_text = "\n".join(cleaned_texts) # Note: manual squash doesn't add marker by default

                if len(combined_text) > 4096:
                    print(f"Aborting: Combined text length ({len(combined_text)}) exceeds limit.")
                    if not args.dry_run: await event.delete()
                    return

                print(f"Squashing {len(messages)} messages.")

                if not args.dry_run:
                    try:
                        if combined_text != target_msg.text:
                            await target_msg.edit(combined_text)
                        msgs_to_delete.append(event.message)
                        await client.delete_messages(chat, msgs_to_delete)
                    except Exception as e:
                        print(f"Failed to squash messages: {e}")
                else:
                    print("[DRY RUN] Would squash.")

            except Exception as e:
                print(f"Error during squash: {e}")

        # --- Real-time: Incoming Message (Boundary Check) ---
        @client.on(events.NewMessage(incoming=True))
        async def incoming_boundary_handler(event):
            if not AUTOSQUASH_ENABLED:
                return

            # If someone else sends a message, it's a boundary.
            # We need to lock the chat to ensure we don't conflict with an outgoing message logic
            async with CHAT_LOCKS[event.chat_id]:
                await strip_marker_from_last_message(client, event.chat_id)

        # --- Real-time: Outgoing Message (Autosquash Logic) ---
        @client.on(events.NewMessage(outgoing=True))
        async def autosquash_watcher(event):
            # Ignore commands
            if event.text.startswith('!squash') or event.text.lower().startswith('!autosquash'):
                return

            # Log message
            chat_title = str(event.chat_id)
            try:
                chat = await event.get_chat()
                chat_title = getattr(chat, 'title', getattr(chat, 'first_name', str(event.chat_id)))
            except:
                pass
            print(f"Sent to {chat_title}: {event.text}")

            if not AUTOSQUASH_ENABLED:
                return

            if args.dry_run:
                return

            async with CHAT_LOCKS[event.chat_id]:
                # 1. Check current message type
                if not is_plain_text(event.message):
                    # Current message is a boundary (media/forward).
                    # Strip marker from previous message and do nothing else.
                    await strip_marker_from_last_message(client, event.chat_id)
                    return

                # 2. Fetch the immediately preceding message
                # offset_id=event.id ensures we get the one before the current one
                prev_msg = None
                async for msg in client.iter_messages(event.chat_id, limit=1, offset_id=event.id):
                    prev_msg = msg
                    break

                # 3. Decision Logic
                should_merge = False

                if prev_msg and prev_msg.out and is_plain_text(prev_msg):
                    # Check if previous message has the marker
                    if prev_msg.text.endswith(MARKER):
                        should_merge = True
                    else:
                        # Previous message exists but has NO marker.
                        # This implies we hit a boundary previously or manually edited it.
                        # Start a new chain.
                        should_merge = False
                else:
                    # Previous message is not ours, or not text. New chain.
                    should_merge = False

                if should_merge:
                    # MERGE
                    # Strip marker from previous message text
                    clean_prev_text = prev_msg.text[:-len(MARKER)]
                    # Combine: Old Text (Clean) + New Message + New Marker
                    new_combined_text = f"{clean_prev_text}\n{event.text}{MARKER}"

                    if len(new_combined_text) <= 4096:
                        try:
                            # 1. Edit previous message to include new text and move the marker
                            await prev_msg.edit(new_combined_text)
                            # 2. Delete the current message
                            await event.delete()
                            print(f"[Autosquash] Merged into message {prev_msg.id}.")
                        except Exception as e:
                            print(f"[Autosquash] Merge failed: {e}. Starting new chain.")
                            try:
                                await event.edit(event.text + MARKER)
                            except: pass
                    else:
                        # Length limit reached. Strip old marker and start fresh on this one.
                        try:
                            await prev_msg.edit(clean_prev_text)
                        except: pass

                        try:
                            await event.edit(event.text + MARKER)
                            print(f"[Autosquash] Limit reached. Started new chain at {event.id}.")
                        except: pass

                else:
                    # START NEW CHAIN
                    try:
                        await event.edit(event.text + MARKER)
                        print(f"[Autosquash] Started new chain at {event.id}.")
                    except Exception as e:
                        print(f"[Autosquash] Failed to mark new message: {e}")

        await client.run_until_disconnected()

if __name__ == '__main__':
    asyncio.run(main())
