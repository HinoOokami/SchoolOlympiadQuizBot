# School Olympiad Quiz Bot

## Overview
This is a Telegram bot for conducting school olympiad quizzes. Users can select topics and answer questions, while administrators can manage the quiz database through Excel file uploads.

## Purpose
- Provide an interactive quiz platform for students via Telegram
- Allow admins to easily manage quiz questions through Excel files
- Support multiple topics and difficulty levels
- Track user progress through conversation states

## Current State
- **Status**: Fully operational
- **Platform**: Telegram bot with Flask webhook server
- **Database**: SQLite (quiz_bot.db)
- **Port**: 5000 (webhook endpoint for Telegram)

## Project Architecture

### Main Components
1. **Telegram Bot** (`python-telegram-bot` library)
   - Handles user interactions
   - Manages conversation flows for quizzes
   - Provides admin panel for data management

2. **Flask Web Server**
   - Receives webhook updates from Telegram
   - Runs on port 5000
   - Endpoints:
     - `/<BOT_TOKEN>` - Webhook endpoint (POST)
     - `/health` - Health check endpoint (GET)
     - `/` - Returns 404 (intentional, not used)

3. **SQLite Database** (`quiz_bot.db`)
   - Tables:
     - `topics` - Quiz topics
     - `questions` - Questions with hints, answers, difficulty
     - `users` - User information

4. **Conversation State Management**
   - Persistent state storage using PicklePersistence
   - Handles multi-step conversations for quizzes and admin tasks

### File Structure
- `SchoolOlympiadQuizBot.py` - Main application file
- `quiz_bot.db` - SQLite database
- `quiz_conversation_states.pkl` - Persistent conversation states
- `requirements.txt` - Python dependencies

## Configuration

### Required Environment Variables
Set these in Replit Secrets:

1. **BOT_TOKEN** (required)
   - Your Telegram Bot API token
   - Get from @BotFather on Telegram
   - Example: `123456789:ABCdefGHIjklMNOpqrsTUVwxyz`

2. **ADMIN_IDS** (required for admin features)
   - Comma-separated list of Telegram user IDs
   - These users can access `/admin` command
   - Get your ID from @userinfobot on Telegram
   - Example: `123456789,987654321`

### Automatic Environment Variables
- `REPLIT_DEV_DOMAIN` - Automatically set by Replit, used for webhook URL
- `PORT` - Defaults to 5000

## How to Use

### For Regular Users
1. Start a chat with your bot on Telegram
2. Send `/start` to begin
3. Select a topic from the menu
4. Use commands:
   - `/hint` - Get a hint for the current question
   - `/answer` - Show the answer
   - `/next` - Move to the next question
   - `/cancel` - Exit the quiz

### For Administrators
1. Send `/admin` to access the admin panel
2. Options:
   - **📁 Upload data**: Replace entire database with new Excel file
   - **📥 Add data**: Append questions from Excel file
   - **🧹 Clear database**: Delete all topics and questions
   - **↩️ Exit**: Return to normal mode

### Excel File Format for Admin
Your Excel file must have these columns:
- **Тема** (Topic) - Required
- **Вопрос** (Question) - Required
- **Подсказка** (Hint) - Required
- **Ответ** (Answer) - Required
- **Сложность** (Difficulty) - Optional (defaults to 'medium')

## Recent Changes
- **2025-10-05**: Migrated from Render to Replit
  - Changed `RENDER_EXTERNAL_URL` to `REPLIT_DEV_DOMAIN`
  - Updated port from 10000 to 5000
  - Changed persistence file path to current directory
  - Made ADMIN_IDS configurable via environment variable

## Dependencies
- python-telegram-bot==22.3 - Telegram Bot API
- openpyxl==3.1.5 - Excel file parsing
- flask==3.0.3 - Web server for webhooks
- gunicorn==22.0.0 - Production WSGI server (optional)

## Development Notes
- The bot uses webhook mode (not polling) for better reliability
- Conversation states persist across restarts
- Database uses foreign key constraints for data integrity
- All logs are visible in the Replit console

## Troubleshooting
- If bot doesn't respond: Check that BOT_TOKEN is set correctly
- If admin commands don't work: Verify your user ID is in ADMIN_IDS
- If webhook fails: Replit will automatically generate a new domain URL on restart
