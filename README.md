# Telegram Draft Reader Bot

This is a simple Telegram User Bot that connects to your account and lists any unsent drafts from your chats.

## Setup

1.  **Get Credentials:**
    *   Go to [https://my.telegram.org](https://my.telegram.org) and log in.
    *   Click on "API development tools".
    *   Create a new application to get your `App api_id` and `App api_hash`.

2.  **Configure:**
    *   Open the `.env` file in this directory.
    *   Fill in your credentials:
        ```env
        TELEGRAM_API_ID=12345678
        TELEGRAM_API_HASH=your_api_hash_here
        ```

3.  **Install Dependencies:**
    (Already done)
    ```bash
    pip install -r requirements.txt
    ```

## Usage

Run the bot:

```bash
python main.py
```

*   On the first run, you will be asked to enter your phone number and the verification code sent to your Telegram account to authenticate the session.
*   Once connected, the bot will scan your dialogs and print any found drafts.
