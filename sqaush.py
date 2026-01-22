import os
import asyncio
import argparse
import collections
import sqlite3
import logging
from datetime import datetime
from telethon import TelegramClient, events
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configure logging to suppress verbose connection errors
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logging.getLogger('telethon').setLevel(logging.WARNING)

# Get credentials from environment variables
API_ID = os.getenv('TELEGRAM_API_ID')
API_HASH = os.getenv('TELEGRAM_API_HASH')
SESSION_NAME = 'draft_bot_session'

# Proxy Configuration
PROXY_TYPE = os.getenv('PROXY_TYPE')
PROXY_ADDR = os.getenv('PROXY_ADDR')
PROXY_PORT = os.getenv('PROXY_PORT')
PROXY_RDNS = os.getenv('PROXY_RDNS', 'True').lower() == 'true'
PROXY_USER = os.getenv('PROXY_USER')
PROXY_PASS = os.getenv('PROXY_PASS')

proxy = None
if PROXY_TYPE and PROXY_ADDR and PROXY_PORT:
    import socks
    proxy_type_map = {
        'socks5': socks.SOCKS5,
        'socks4': socks.SOCKS4,
        'http': socks.HTTP,
    }
    ptype = proxy_type_map.get(PROXY_TYPE.lower())
    if ptype:
        proxy = (ptype, PROXY_ADDR, int(PROXY_PORT), PROXY_RDNS, PROXY_USER, PROXY_PASS)
        print(f"Using Proxy: {PROXY_TYPE}://{PROXY_ADDR}:{PROXY_PORT}")

# Configuration
MARKER = "\n<<<"
AUTOSQUASH_ENABLED = False
# Lock to prevent race conditions per chat
CHAT_LOCKS = collections.defaultdict(asyncio.Lock)
DB_NAME = "deleted_messages.db"

if not API_ID or not API_HASH:
    print("Error: TELEGRAM_API_ID and TELEGRAM_API_HASH must be set in a .env file.")
    exit(1)

def parse_arguments():
    parser = argparse.ArgumentParser(description="Telegram Draft & Squash Bot")
    parser.add_argument('-d', '--dry-run', action='store_true', help="Print actions without executing them")
    return parser.parse_args()

def init_db():
    """Initialize the SQLite database for deleted messages."""
    try:
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS deleted_messages
                     (id INTEGER PRIMARY KEY AUTOINCREMENT,
                      chat_id INTEGER,
                      message_id INTEGER,
                      sender_id INTEGER,
                      text TEXT,
                      sent_date TEXT,
                      deleted_at TEXT)''')
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Database init error: {e}")

def archive_messages(messages):
    """Save messages to SQLite before deletion."""
    if not messages:
        return
    if not isinstance(messages, list):
        messages = [messages]

    data = []
    now = datetime.now().isoformat()

    for m in messages:
        try:
            # Safe attribute access
            m_id = getattr(m, 'id', None)
            c_id = getattr(m, 'chat_id', None)
            s_id = getattr(m, 'sender_id', None)
            text = getattr(m, 'text', "")
            date_obj = getattr(m, 'date', None)
            sent_date = date_obj.isoformat() if date_obj else None

            data.append((c_id, m_id, s_id, text, sent_date, now))
        except Exception as e:
            print(f"Error preparing message {getattr(m, 'id', '?')} for archive: {e}")

    if not data:
        return

    try:
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.executemany('''INSERT INTO deleted_messages
                         (chat_id, message_id, sender_id, text, sent_date, deleted_at)
                         VALUES (?, ?, ?, ?, ?, ?)''', data)
        conn.commit()
        conn.close()
        # print(f"Archived {len(data)} messages.") # Optional: too verbose for every delete
    except Exception as e:
        print(f"CRITICAL: Failed to archive messages to DB: {e}")

async def safe_delete(client, chat, messages, dry_run=False):
    """Archives messages then deletes them."""
    if not messages:
        return

    if not isinstance(messages, list):
        messages = [messages]

    if dry_run:
        print(f"[DRY RUN] Would archive and delete {len(messages)} message(s). IDs: {[m.id for m in messages]}")
        return

    # 1. Archive
    archive_messages(messages)

    # 2. Delete
    try:
        await client.delete_messages(chat, messages)
    except Exception as e:
        print(f"Delete failed: {e}")

def is_plain_text(message):
    """Returns True if message is strictly plain text (no media, no forwards, has text)."""
    return bool(message.text and not message.media and not message.fwd_from)

async def strip_marker_from_last_message(client, chat_id):
    """Helper to find the last marked message by me in a chat and strip the marker."""
    async for msg in client.iter_messages(chat_id, from_user='me', limit=10):
        if msg.text and msg.text.endswith(MARKER):
            new_text = msg.text[:-len(MARKER)]
            try:
                await msg.edit(new_text)
                print(f"[Autosquash] Boundary hit. Removed marker from message {msg.id}.")
            except Exception as e:
                print(f"[Autosquash] Failed to strip marker: {e}")
            return

async def main():
    # Initialize DB
    init_db()

    args = parse_arguments()

    async with TelegramClient(SESSION_NAME, int(API_ID), API_HASH, proxy=proxy) as client:
        print(f"Connected! (Dry Run: {args.dry_run})")
        print(f"Message backup: {DB_NAME}")
        print("Listening for commands...")
        print("  !squash [n]        -> Merge messages")
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

            # Delete the status message after a few seconds
            await asyncio.sleep(3)
            await safe_delete(client, event.chat_id, [event.message], dry_run=args.dry_run)

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
                        await safe_delete(client, event.chat_id, [event.message], dry_run=args.dry_run)
                        return

                    async for msg in client.iter_messages(chat, from_user='me', limit=n, offset_id=event.id):
                        if not is_plain_text(msg):
                            print(f"Aborting: Message {msg.id} in fixed range {n} is not plain text.")
                            await safe_delete(client, event.chat_id, [event.message], dry_run=args.dry_run)
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
                    await safe_delete(client, event.chat_id, [event.message], dry_run=args.dry_run)
                    return

                messages.reverse()

                # Cleanup markers
                cleaned_texts = []
                for m in messages:
                    txt = m.text
                    if txt.endswith(MARKER):
                        txt = txt[:-len(MARKER)]
                    cleaned_texts.append(txt)

                target_msg = messages[0]
                msgs_to_delete = messages[1:]
                combined_text = "\n".join(cleaned_texts)

                if len(combined_text) > 4096:
                    print(f"Aborting: Combined text length ({len(combined_text)}) exceeds limit.")
                    await safe_delete(client, event.chat_id, [event.message], dry_run=args.dry_run)
                    return

                print(f"Squashing {len(messages)} messages.")

                if not args.dry_run:
                    try:
                        if combined_text != target_msg.text:
                            await target_msg.edit(combined_text)

                        # Add command message to deletion list
                        msgs_to_delete.append(event.message)

                        # Use safe_delete for all
                        await safe_delete(client, chat, msgs_to_delete, dry_run=args.dry_run)

                    except Exception as e:
                        print(f"Failed to squash messages: {e}")
                else:
                    print("[DRY RUN] Would edit oldest message and delete others.")
                    print(f"[DRY RUN] Would delete {len(msgs_to_delete) + 1} messages (including command).")

            except Exception as e:
                print(f"Error during squash: {e}")

        # --- Real-time: Incoming Message (Boundary Check) ---
        @client.on(events.NewMessage(incoming=True))
        async def incoming_boundary_handler(event):
            if not AUTOSQUASH_ENABLED:
                return
            async with CHAT_LOCKS[event.chat_id]:
                await strip_marker_from_last_message(client, event.chat_id)

        # --- Real-time: Outgoing Message (Autosquash Logic) ---
        @client.on(events.NewMessage(outgoing=True))
        async def autosquash_watcher(event):
            if event.text.startswith('!squash') or event.text.lower().startswith('!autosquash'):
                return

            try:
                chat = await event.get_chat()
                chat_title = getattr(chat, 'title', getattr(chat, 'first_name', str(event.chat_id)))
            except:
                chat_title = str(event.chat_id)
            print(f"Sent to {chat_title}: {event.text}")

            if not AUTOSQUASH_ENABLED:
                return

            # Dry run handled inside logic via args.dry_run usage in actions if needed,
            # but usually autosquash shouldn't run in dry run mode?
            # The prompt asked for -d arg, which we have.
            if args.dry_run:
                return

            async with CHAT_LOCKS[event.chat_id]:
                if not is_plain_text(event.message):
                    await strip_marker_from_last_message(client, event.chat_id)
                    return

                prev_msg = None
                async for msg in client.iter_messages(event.chat_id, limit=1, offset_id=event.id):
                    prev_msg = msg
                    break

                should_merge = False
                if prev_msg and prev_msg.out and is_plain_text(prev_msg):
                    if prev_msg.text.endswith(MARKER):
                        should_merge = True

                if should_merge:
                    # MERGE
                    clean_prev_text = prev_msg.text[:-len(MARKER)]
                    new_combined_text = f"{clean_prev_text}\n{event.text}{MARKER}"

                    if len(new_combined_text) <= 4096:
                        try:
                            await prev_msg.edit(new_combined_text)

                            # DELETE with backup
                            await safe_delete(client, event.chat_id, [event.message], dry_run=args.dry_run)

                            print(f"[Autosquash] Merged into message {prev_msg.id}.")
                        except Exception as e:
                            print(f"[Autosquash] Merge failed: {e}. Starting new chain.")
                            try:
                                await event.edit(event.text + MARKER)
                            except: pass
                    else:
                        # Limit reached
                        try:
                            await prev_msg.edit(clean_prev_text)
                        except: pass
                        try:
                            await event.edit(event.text + MARKER)
                            print(f"[Autosquash] Limit reached. Started new chain.")
                        except: pass
                else:
                    # NEW CHAIN
                    try:
                        await event.edit(event.text + MARKER)
                        print(f"[Autosquash] Started new chain at {event.id}.")
                    except Exception as e:
                        print(f"[Autosquash] Failed to mark new message: {e}")

        await client.run_until_disconnected()

if __name__ == '__main__':
    asyncio.run(main())
