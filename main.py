import os
import asyncio
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

async def main():
    async with TelegramClient(SESSION_NAME, int(API_ID), API_HASH) as client:
        print("Connected! Listening for squash commands...")
        print("Usage: Send '!squash n' in any chat to merge your last n messages.")

        @client.on(events.NewMessage(outgoing=True, pattern=r'!squash (\d+)'))
        async def squash_handler(event):
            try:
                n = int(event.pattern_match.group(1))
                if n < 1:
                    await event.delete()
                    return

                chat = await event.get_chat()
                chat_name = getattr(chat, 'title', getattr(chat, 'first_name', str(chat.id)))
                print(f"Squashing last {n} messages in {chat_name}...")

                # Fetch messages specifically older than the command message (offset_id)
                # This prevents accidentally squashing new messages that come in during processing
                messages = []
                async for msg in client.iter_messages(chat, from_user='me', limit=n, offset_id=event.id):
                    messages.append(msg)

                if not messages:
                    print("No messages found to squash.")
                    await event.delete()
                    return

                # messages are Newest -> Oldest. Reverse to preserve chronological order.
                messages.reverse()
                
                target_msg = messages[0]
                msgs_to_delete = messages[1:]

                # Combine text, ignoring empty ones
                combined_text = "\n".join([m.text for m in messages if m.text])

                # Edit the oldest message with the combined text
                if combined_text and combined_text != target_msg.text:
                    await target_msg.edit(combined_text)

                # Add the command message itself to the deletion list
                msgs_to_delete.append(event.message)

                # Delete all other messages + command in one go
                if msgs_to_delete:
                    await client.delete_messages(chat, msgs_to_delete)

                print(f"Successfully squashed {len(messages)} messages.")

            except Exception as e:
                print(f"Error during squash: {e}")

        await client.run_until_disconnected()

if __name__ == '__main__':
    asyncio.run(main())
