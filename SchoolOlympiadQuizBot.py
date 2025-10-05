import os
import logging
import sqlite3
from urllib.parse import urlparse
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application, CommandHandler, MessageHandler, 
    ContextTypes, ConversationHandler, filters, PicklePersistence
)
import tempfile
import openpyxl
from flask import Flask, request, Response
import asyncio
import json

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Состояния для ConversationHandler
(CHOOSE_TOPIC, QUESTION, HINT, ANSWER, 
 ADMIN_MENU, ADMIN_UPLOAD_REPLACE, ADMIN_UPLOAD_APPEND, ADMIN_CONFIRM_CLEAR) = range(8)

# Инициализация Flask приложения
app = Flask(__name__)
application = None  # Глобальная переменная для Application

class QuizBot:
    def __init__(self, admin_ids):
        self.db_path = 'quiz_bot.db'  # Persistent path on Replit root
        self.admin_ids = admin_ids
        self.init_database()
        self.user_states = {}

    def init_database(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA foreign_keys = ON")
        c = conn.cursor()
        
        c.execute('''CREATE TABLE IF NOT EXISTS topics
                     (id INTEGER PRIMARY KEY AUTOINCREMENT,
                      name TEXT UNIQUE)''')
        
        c.execute('''CREATE TABLE IF NOT EXISTS questions
                     (id INTEGER PRIMARY KEY AUTOINCREMENT,
                      topic_id INTEGER,
                      question_text TEXT,
                      hint TEXT,
                      answer TEXT,
                      difficulty TEXT DEFAULT 'medium',
                      FOREIGN KEY (topic_id) REFERENCES topics (id) ON DELETE CASCADE)''')
        
        conn.commit()
        conn.close()

    def save_user_to_db(self, user):
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        
        c.execute('''CREATE TABLE IF NOT EXISTS users
                     (id INTEGER PRIMARY KEY,
                      first_name TEXT,
                      username TEXT)''')
        
        c.execute('''INSERT OR IGNORE INTO users (id, first_name, username)
                     VALUES (?, ?, ?)''',
                 (user.id, user.first_name, user.username))
        
        conn.commit()
        conn.close()

    def parse_excel_file(self, file_path, replace=True):
        try:
            logger.info(f"Parsing file: {file_path}, replace={replace}")

            workbook = openpyxl.load_workbook(file_path)
            sheet = workbook.active
            
            headers = []
            for cell in sheet[1]:
                headers.append(cell.value)
            
            required_headers = ['Тема', 'Вопрос', 'Подсказка', 'Ответ']
            for req in required_headers:
                if req not in headers:
                    raise ValueError(f"Отсутствует колонка: {req}")
            
            conn = sqlite3.connect(self.db_path)
            c = conn.cursor()
            
            if replace:
                c.execute("DELETE FROM questions")
                c.execute("DELETE FROM topics")
            
            for row_num in range(2, sheet.max_row + 1):
                row_data = []
                for col_num in range(1, len(headers) + 1):
                    cell_value = sheet.cell(row=row_num, column=col_num).value
                    row_data.append(cell_value)
                
                if not any(row_data):
                    continue
                
                if len(row_data) < len(required_headers):
                    logger.warning(f"Пропущена строка {row_num}: недостаточно колонок")
                    continue
                
                topic_name = row_data[headers.index('Тема')]
                question_text = row_data[headers.index('Вопрос')]
                hint = row_data[headers.index('Подсказка')]
                answer = row_data[headers.index('Ответ')]
                
                if not all([topic_name, question_text, answer]):
                    logger.warning(f"Пропущена строка {row_num}: отсутствуют обязательные поля")
                    continue
                
                difficulty = 'medium'
                if 'Сложность' in headers:
                    difficulty = row_data[headers.index('Сложность')] or 'medium'
                
                if not str(topic_name).strip():
                    logger.warning(f"Пропущена строка {row_num}: пустая тема")
                    continue
                
                c.execute("INSERT OR IGNORE INTO topics (name) VALUES (?)", (topic_name,))
                c.execute("SELECT id FROM topics WHERE name = ?", (topic_name,))
                topic_id = c.fetchone()[0]
                
                c.execute('''INSERT INTO questions (topic_id, question_text, hint, answer, difficulty)
                             VALUES (?, ?, ?, ?, ?)''',
                         (topic_id, question_text, hint, answer, difficulty))
            
            conn.commit()
            conn.close()
            logger.info(f"Inserted {len(inserted_topics)} topics")  # Add after loop
            return True
            
        except Exception as e:
            logger.error(f"Error parsing Excel file: {e}")
            return False

    def clear_database(self):
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("DELETE FROM questions")
        c.execute("DELETE FROM topics")
        conn.commit()
        conn.close()

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        self.save_user_to_db(user)
        
        topics = self.get_topics_from_db()
        if not topics:
            await update.message.reply_text(
                f"Привет, {user.first_name}! Я бот для викторин.\n\n"
                "На данный момент нет доступных тем. Обратитесь к администратору."
            )
            return ConversationHandler.END
        
        keyboard = [[topic] for topic in topics]
        reply_markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True)
        await update.message.reply_text(
            f"Привет, {user.first_name}! Я бот для викторин.\n\n"
            "Выберите тему для вопросов:",
            reply_markup=reply_markup
        )
        return CHOOSE_TOPIC

    async def upload_file(self, update: Update, context: ContextTypes.DEFAULT_TYPE, replace=True):
        if update.message.document:
            file = await update.message.document.get_file()
            file_extension = os.path.splitext(update.message.document.file_name)[1].lower()
            
            if file_extension not in ['.xls', '.xlsx']:
                await update.message.reply_text("Пожалуйста, отправьте файл в формате XLS или XLSX.")
                return ADMIN_UPLOAD_REPLACE if replace else ADMIN_UPLOAD_APPEND
            
            with tempfile.NamedTemporaryFile(delete=False, suffix=file_extension) as tmp_file:
                await file.download_to_drive(tmp_file.name)
                
                success = self.parse_excel_file(tmp_file.name, replace=replace)
                os.unlink(tmp_file.name)
                
                if success:
                    logger.info(f"Upload successful: {replace=}, topics added")
                    await update.message.reply_text(
                        f"Данные успешно {'заменены' if replace else 'добавлены'} в базу!",
                        reply_markup=ReplyKeyboardRemove()  # Remove keyboard
                    )
                    return ConversationHandler.END  # End conversation to reset state
                else:
                    logger.error("Upload failed: Excel parsing error")
                    await update.message.reply_text("Ошибка при чтении Excel-файла. Проверьте формат.", reply_markup=ReplyKeyboardMarkup([['↩️ Отмена']], one_time_keyboard=True))
                    return ADMIN_UPLOAD_REPLACE if replace else ADMIN_UPLOAD_APPEND
        else:
            await update.message.reply_text("Пожалуйста, отправьте XLS/XLSX файл.", reply_markup=ReplyKeyboardMarkup([['↩️ Отмена']], one_time_keyboard=True))
            return ADMIN_UPLOAD_REPLACE if replace else ADMIN_UPLOAD_APPEND

    def get_topics_from_db(self):
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        
        c.execute("SELECT name FROM topics ORDER BY name")
        topics = [row[0] for row in c.fetchall()]
        
        conn.close()
        return topics

    def get_questions_for_topic(self, topic_name):
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        
        c.execute('''SELECT q.id, q.question_text, q.hint, q.answer, q.difficulty
                     FROM questions q
                     JOIN topics t ON q.topic_id = t.id
                     WHERE LOWER(t.name) = LOWER(?)
                     ORDER BY q.id''', (topic_name,))
        
        questions = []
        for row in c.fetchall():
            questions.append({
                'id': row[0],
                'text': row[1],
                'hint': row[2],
                'answer': row[3],
                'difficulty': row[4]
            })
        
        conn.close()
        return questions

    async def choose_topic(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        topic_name = update.message.text
        user_id = update.effective_user.id
        
        if user_id not in self.user_states:
            self.user_states[user_id] = {}
        
        self.user_states[user_id]['current_topic'] = topic_name
        questions = self.get_questions_for_topic(topic_name)
        self.user_states[user_id]['questions'] = questions
        self.user_states[user_id]['current_question_index'] = 0
        
        if questions:
            question = questions[0]
            await update.message.reply_text(
                f"📚 Тема: {topic_name}\n"
                f"💡 Сложность: {question.get('difficulty', 'medium')}\n\n"
                f"❓ Вопрос: {question['text']}\n\n"
                "Используйте:\n"
                "/hint - для подсказки\n"
                "/answer - для ответа\n"
                "/next - следующий вопрос",
                reply_markup=ReplyKeyboardMarkup([['/hint', '/answer', '/next']], one_time_keyboard=True)
            )
            logger.info(f"Topic chosen by user {user_id}: {topic_name}, questions loaded: {len(questions)}")
            return QUESTION
        else:
            await update.message.reply_text("В этой теме нет вопросов.", reply_markup=ReplyKeyboardRemove())
            logger.warning(f"No questions for topic {topic_name} chosen by user {user_id}")
            return CHOOSE_TOPIC

    async def show_question(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id in self.user_states:
            questions = self.user_states[user_id]['questions']
            current_index = self.user_states[user_id]['current_question_index']
            
            if current_index < len(questions):
                question = questions[current_index]
                topic = self.user_states[user_id]['current_topic']
                
                await update.message.reply_text(
                    f"📚 Тема: {topic}\n"
                    f"💡 Сложность: {question.get('difficulty', 'medium')}\n\n"
                    f"❓ Вопрос: {question['text']}\n\n"
                    "Используйте:\n"
                    "/hint - для подсказки\n"
                    "/answer - для ответа\n"
                    "/next - следующий вопрос",
                    reply_markup=ReplyKeyboardMarkup([['/hint', '/answer', '/next']], one_time_keyboard=True)
                )
                return QUESTION
            else:
                await update.message.reply_text("🎉 Вопросы в этой теме закончились!")
                return CHOOSE_TOPIC
        else:
            await update.message.reply_text("Сначала выберите тему!")
            return CHOOSE_TOPIC

    async def show_hint(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id in self.user_states:
            questions = self.user_states[user_id]['questions']
            current_index = self.user_states[user_id]['current_question_index']
            
            if current_index < len(questions):
                question = questions[current_index]
                
                await update.message.reply_text(
                    f"💡 Подсказка: {question['hint']}\n\n"
                    "Используйте:\n"
                    "/answer - для ответа\n"
                    "/next - следующий вопрос",
                    reply_markup=ReplyKeyboardMarkup([['/answer', '/next']], one_time_keyboard=True)
                )
                logger.info(f"Hint shown for user {user_id}, question {current_index}")
                return HINT
        await update.message.reply_text("Ошибка при получении подсказки. Вернитесь к вопросу.", 
                                    reply_markup=ReplyKeyboardMarkup([['/hint', '/answer', '/next']], one_time_keyboard=True))
        return QUESTION

    async def show_answer(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id in self.user_states:
            questions = self.user_states[user_id]['questions']
            current_index = self.user_states[user_id]['current_question_index']
            
            if current_index < len(questions):
                question = questions[current_index]
                
                await update.message.reply_text(
                    f"✅ Ответ: {question['answer']}\n\n"
                    "/next - следующий вопрос",
                    reply_markup=ReplyKeyboardMarkup([['/next']], one_time_keyboard=True)
                )
                logger.info(f"Answer shown for user {user_id}, question {current_index}")
                return ANSWER
        await update.message.reply_text("Ошибка при получении ответа. Вернитесь к вопросу.", 
                                    reply_markup=ReplyKeyboardMarkup([['/hint', '/answer', '/next']], one_time_keyboard=True))
        return QUESTION

    async def next_question(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id in self.user_states:
            self.user_states[user_id]['current_question_index'] += 1
            logger.info(f"Next question for user {user_id}, index now {self.user_states[user_id]['current_question_index']}")
            return await self.show_question(update, context)
        return CHOOSE_TOPIC

    async def cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        self.user_states.pop(update.effective_user.id, None)
        logger.info(f"Conversation canceled by user {update.effective_user.id}")
        await update.message.reply_text("Операция отменена.", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END

    async def admin_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id not in self.admin_ids:
            await update.message.reply_text("❌ Доступ запрещен. Вы не администратор.")
            return ConversationHandler.END
        
        keyboard = [
            ['📁 Загрузить данные', '📥 Дополнить данные'],
            ['🧹 Очистить базу', '↩️ Выйти из админ-режима']
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=False, resize_keyboard=True)
        await update.message.reply_text(
            "🛡️ Добро пожаловать в админ-панель!\n\n"
            "Ваши возможности:\n"
            "• 📁 Загрузить данные: Заменить все данные в базе новым XLS/XLSX файлом.\n"
            "• 📥 Дополнить данные: Добавить новые вопросы из XLS/XLSX файла.\n"
            "• 🧹 Очистить базу: Удалить все темы и вопросы.\n"
            "• ↩️ Выйти из админ-режима: Вернуться к обычному режиму.\n\n"
            "Выберите действие:",
            reply_markup=reply_markup
        )
        return ADMIN_MENU

    async def admin_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        choice = update.message.text
        user_id = update.effective_user.id
        
        if user_id not in self.admin_ids:
            await update.message.reply_text("❌ Доступ запрещен. Вы не администратор.", reply_markup=ReplyKeyboardRemove())
            return ConversationHandler.END
        
        if choice == "↩️ Выйти из админ-режима":
            await update.message.reply_text(
                "✅ Вы вышли из админ-режима",
                reply_markup=ReplyKeyboardRemove()  # Ensure keyboard is removed
            )
            self.user_states.pop(user_id, None)  # Clear user state to reset conversation
            return ConversationHandler.END
        
        elif choice == "📁 Загрузить данные":
            await update.message.reply_text(
                "📁 Отправьте XLS/XLSX файл для ЗАМЕНЫ базы данных\n\n"
                "Формат файла должен содержать колонки:\n"
                "• Тема\n• Вопрос\n• Подсказка\n• Ответ\n• Сложность (опционально)",
                reply_markup=ReplyKeyboardMarkup([['↩️ Отмена']], one_time_keyboard=True)
            )
            return ADMIN_UPLOAD_REPLACE
        
        elif choice == "📥 Дополнить данные":
            await update.message.reply_text(
                "📥 Отправьте XLS/XLSX файл для ДОПОЛНЕНИЯ базы данных\n\n"
                "Формат файла должен содержать колонки:\n"
                "• Тема\n• Вопрос\n• Подсказка\n• Ответ\n• Сложность (опционально)",
                reply_markup=ReplyKeyboardMarkup([['↩️ Отмена']], one_time_keyboard=True)
            )
            return ADMIN_UPLOAD_APPEND
        
        elif choice == "🧹 Очистить базу":
            keyboard = [['✅ Да, очистить', '❌ Нет, отмена']]
            reply_markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True)
            await update.message.reply_text(
                "⚠️ ВНИМАНИЕ! Эта операция удалит ВСЕ данные из базы.\n"
                "Вы уверены, что хотите продолжить?",
                reply_markup=reply_markup
            )
            return ADMIN_CONFIRM_CLEAR
        
        # If choice is invalid, keep the admin panel active
        keyboard = [
            ['📁 Загрузить данные', '📥 Дополнить данные'],
            ['🧹 Очистить базу', '↩️ Выйти из админ-режима']
        ]
        await update.message.reply_text(
            "Пожалуйста, выберите действие из меню:",
            reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=False, resize_keyboard=True)
        )
        return ADMIN_MENU

    async def admin_upload_file(self, update: Update, context: ContextTypes.DEFAULT_TYPE, replace=True):
        if update.message.text == "↩️ Отмена":
            return await self.admin_menu(update, context)
        
        return await self.upload_file(update, context, replace=replace)

    async def admin_confirm_clear(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.message.text == "✅ Да, очистить":
            self.clear_database()
            logger.info(f"Database cleared by admin {update.effective_user.id}")
            await update.message.reply_text(
                "🧹 База данных успешно очищена!",
                reply_markup=ReplyKeyboardRemove()  # Remove keyboard
            )
            return ConversationHandler.END
        else:
            await update.message.reply_text(
                "Операция отменена.",
                reply_markup=ReplyKeyboardRemove()  # Remove keyboard on cancel
            )
            return ADMIN_MENU

# Flask routes
@app.route('/health')
def health():
    logger.info("Health check endpoint called")
    return 'OK', 200

@app.route('/')
def root():
    logger.info("Root endpoint called")
    return 'Not Found', 404

@app.route('/<token>', methods=['POST'])
def webhook(token):
    global application
    if token != os.getenv("BOT_TOKEN"):
        logger.warning("Invalid token received")
        return Response("Invalid token", status=403)
    
    if application is None:
        logger.error("Application not initialized")
        return Response("Internal server error: Application not initialized", status=500)
    
    try:
        data = request.get_json()
        logger.info(f"Received webhook data: {json.dumps(data, ensure_ascii=False)}")
        update = Update.de_json(data, application.bot)
        if update:
            asyncio.run(application.process_update(update))
            logger.info("Webhook update processed successfully")
            return Response("OK", status=200)
        else:
            logger.error("Failed to parse update from webhook data")
            return Response("Invalid update data", status=400)
    except Exception as e:
        logger.error(f"Error processing webhook: {str(e)}")
        return Response(f"Error: {str(e)}", status=500)

async def init_application():
    global application
    TOKEN = os.getenv("BOT_TOKEN")
    REPLIT_DOMAIN = os.getenv("REPLIT_DEV_DOMAIN")
    
    if not TOKEN:
        logger.error("BOT_TOKEN не установлен")
        raise ValueError("BOT_TOKEN не установлен!")
    if not REPLIT_DOMAIN:
        logger.error("REPLIT_DEV_DOMAIN не установлен")
        raise ValueError("REPLIT_DEV_DOMAIN не установлен!")
    
    webhook_url = f'https://{REPLIT_DOMAIN}/{TOKEN}'
    logger.info(f"Environment: BOT_TOKEN={TOKEN[:4]}..., WEBHOOK_URL={webhook_url}, PORT={os.environ.get('PORT', 5000)}")
    
    admin_ids_str = os.getenv("ADMIN_IDS", "")
    admin_ids = [int(id.strip()) for id in admin_ids_str.split(',') if id.strip().isdigit()]
    if not admin_ids:
        logger.warning("No ADMIN_IDS configured, admin features will be unavailable")
    
    quiz_bot = QuizBot(admin_ids=admin_ids)
    persistence = PicklePersistence(filepath="quiz_conversation_states.pkl")
    application = Application.builder().token(TOKEN).persistence(persistence).build()
    
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', quiz_bot.start)],
        states={
            CHOOSE_TOPIC: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, quiz_bot.choose_topic)
            ],
            QUESTION: [
                CommandHandler('hint', quiz_bot.show_hint),
                CommandHandler('answer', quiz_bot.show_answer),
                CommandHandler('next', quiz_bot.next_question),
                MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: u.message.reply_text(
                    "Используйте команды:\n/hint - подсказка\n/answer - ответ\n/next - следующий вопрос"
                ))
            ],
            HINT: [
                CommandHandler('answer', quiz_bot.show_answer),
                CommandHandler('next', quiz_bot.next_question),
                MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: u.message.reply_text(
                    "Используйте команды:\n/answer - ответ\n/next - следующий вопрос"
                ))
            ],
            ANSWER: [
                CommandHandler('next', quiz_bot.next_question),
                MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: u.message.reply_text(
                    "Используйте команду /next для перехода к следующему вопросу"
                ))
            ]
        },
        fallbacks=[CommandHandler('cancel', quiz_bot.cancel)],
        name="main_conversation",
        persistent=True
    )
    
    admin_handler = ConversationHandler(
        entry_points=[CommandHandler('admin', quiz_bot.admin_start)],
        states={
            ADMIN_MENU: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, quiz_bot.admin_menu)
            ],
            ADMIN_UPLOAD_REPLACE: [
                MessageHandler(filters.Document.ALL, quiz_bot.admin_upload_file),
                MessageHandler(filters.TEXT & ~filters.COMMAND, quiz_bot.admin_upload_file)
            ],
            ADMIN_UPLOAD_APPEND: [
                MessageHandler(filters.Document.ALL, lambda u, c: quiz_bot.admin_upload_file(u, c, replace=False)),
                MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: quiz_bot.admin_upload_file(u, c, replace=False))
            ],
            ADMIN_CONFIRM_CLEAR: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, quiz_bot.admin_confirm_clear)
            ]
        },
        fallbacks=[CommandHandler('cancel', quiz_bot.cancel)],
        name="admin_conversation",
        persistent=True
    )
    
    application.add_handler(conv_handler)
    application.add_handler(admin_handler)
    
    logger.info(f"Setting webhook URL: {webhook_url}")
    await application.bot.delete_webhook()
    await application.bot.set_webhook(url=webhook_url)
    webhook_info = await application.bot.get_webhook_info()
    logger.info(f"Webhook info: {webhook_info}")
    
    if webhook_info.url != webhook_url:
        logger.error(f"Webhook setup failed: expected {webhook_url}, got {webhook_info.url}")
        raise RuntimeError("Webhook setup failed")
    
    await application.initialize()
    await application.start()

def run_flask():
    port = int(os.environ.get('PORT', 5000))
    logger.info(f"Starting Flask on port {port}")
    app.run(host='0.0.0.0', port=port)

# Initialize application at module level for Gunicorn
asyncio.run(init_application())

if __name__ == '__main__':
    run_flask()