import os
import asyncio
import argparse
from telethon import TelegramClient, events
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Get credentials from environment variables
API_ID = os.getenv('TELEGRAM_API_ID')
API_HASH = os.getenv('TELEGRAM_API_HASH')
SESSION_NAME = 'draft_bot_session'

if not API_ID or not API_HASH:
    print("Error: TELEGRAM_API_ID and TELEGRAM_API_HASH must be set in a .env file.")
    exit(1)

def parse_arguments():
    parser = argparse.ArgumentParser(description="Telegram Draft & Squash Bot")
    parser.add_argument('-d', '--dry-run', action='store_true', help="Print actions without executing them")
    return parser.parse_args()

async def main():
    args = parse_arguments()
    
    async with TelegramClient(SESSION_NAME, int(API_ID), API_HASH) as client:
        print(f"Connected! (Dry Run: {args.dry_run})")
        print("Listening for squash commands...")
        print("Usage:")
        print("  !squash n  -> Merge last n messages")
        print("  !squash    -> Merge all consecutive messages sent by you")

        # Regex: matches '!squash' optionally followed by whitespace and digits
        @client.on(events.NewMessage(outgoing=True, pattern=r'^!squash(?:\s+(\d+))?\s*$'))
        async def squash_handler(event):
            try:
                # Group 1 is the number (or None)
                n_str = event.pattern_match.group(1)
                
                chat = await event.get_chat()
                chat_name = getattr(chat, 'title', getattr(chat, 'first_name', str(chat.id)))
                
                messages = []
                
                if n_str:
                    # Case 1: Fixed number provided
                    n = int(n_str)
                    print(f"Command: !squash {n} in {chat_name}")
                    if n < 1:
                        if not args.dry_run: await event.delete()
                        return

                    # Fetch exactly n messages older than command
                    async for msg in client.iter_messages(chat, from_user='me', limit=n, offset_id=event.id):
                        # Strict check: If any message in fixed range is NOT plain text, we stop/abort for safety
                        if not (msg.text and not msg.media and not msg.fwd_from):
                            print(f"Aborting: Message {msg.id} in fixed range {n} is not plain text.")
                            if not args.dry_run: await event.delete()
                            return
                        messages.append(msg)
                else:
                    # Case 2: Smart squash (consecutive plain text messages)
                    print(f"Command: !squash (smart) in {chat_name}")
                    
                    # Fetch messages until we hit a boundary (other user OR non-plain-text)
                    async for msg in client.iter_messages(chat, limit=100, offset_id=event.id):
                        is_plain_text = msg.out and msg.text and not msg.media and not msg.fwd_from
                        if is_plain_text:
                            messages.append(msg)
                        else:
                            # Boundary reached (other user, media, forward, etc.)
                            break
                            
                if not messages:
                    print("No messages found to squash.")
                    if not args.dry_run: await event.delete()
                    return

                # Messages are Newest -> Oldest. Reverse to Chronological (Oldest -> Newest)
                messages.reverse()
                
                target_msg = messages[0]
                msgs_to_delete = messages[1:]

                # Combine text
                combined_text = "\n".join([m.text for m in messages if m.text])
                
                # Check Telegram's message length limit (4096 characters)
                if len(combined_text) > 4096:
                    print(f"Aborting: Combined text length ({len(combined_text)}) exceeds Telegram limit (4096).")
                    if not args.dry_run: await event.delete()
                    return
                
                # --- Execution / Dry Run ---
                
                print(f"Found {len(messages)} messages to squash.")
                print(f"Target Message ID: {target_msg.id} (Oldest)")
                print(f"Messages to delete: {[m.id for m in msgs_to_delete]}")
                print(f"Command message ID to delete: {event.id}")
                
                if args.dry_run:
                    print("\n[DRY RUN] Would update message:")
                    print(f"From: {target_msg.text!r}")
                    print(f"To:   {combined_text!r}")
                    print(f"[DRY RUN] Would delete {len(msgs_to_delete) + 1} messages (including command).")
                else:
                    try:
                        # 1. Edit the oldest message
                        if combined_text and combined_text != target_msg.text:
                            await target_msg.edit(combined_text)

                        # 2. Delete the others + the command message
                        msgs_to_delete.append(event.message)
                        await client.delete_messages(chat, msgs_to_delete)
                        print("Squash complete.")
                    except Exception as e:
                        print(f"Failed to squash messages: {e}")
                        # If edit failed, we might still want to delete the command, 
                        # but safer to leave it so user knows something failed.

            except Exception as e:
                print(f"Error during squash: {e}")

        # Real-time outgoing message logger (optional, keeping it as it was useful)
        @client.on(events.NewMessage(outgoing=True))
        async def log_handler(event):
            # Ignore squash commands to avoid double printing or clutter
            if event.text.startswith('!squash'):
                return
                
            chat_title = "Unknown"
            try:
                chat = await event.get_chat()
                chat_title = getattr(chat, 'title', getattr(chat, 'first_name', str(event.chat_id)))
            except:
                chat_title = str(event.chat_id)
            print(f"Sent to {chat_title}: {event.text}")

        await client.run_until_disconnected()

if __name__ == '__main__':
    asyncio.run(main())