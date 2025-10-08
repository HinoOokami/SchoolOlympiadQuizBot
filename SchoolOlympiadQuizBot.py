import os
import logging
import sqlite3
import tempfile
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    ContextTypes, ConversationHandler, filters, PicklePersistence
)
import openpyxl

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Состояния
(CHOOSE_TOPIC, QUESTION, HINT, ANSWER,
 ADMIN_MENU, ADMIN_UPLOAD_REPLACE, ADMIN_UPLOAD_APPEND, ADMIN_CONFIRM_CLEAR) = range(8)


class QuizBot:
    def __init__(self, admin_ids):
        self.db_path = 'quiz_bot.db'
        self.admin_ids = admin_ids
        self.init_database()
        self.user_states = {}

    def init_database(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA foreign_keys = ON")
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS topics
                     (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE)''')
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
                     (id INTEGER PRIMARY KEY, first_name TEXT, username TEXT)''')
        c.execute('''INSERT OR IGNORE INTO users (id, first_name, username)
                     VALUES (?, ?, ?)''', (user.id, user.first_name, user.username))
        conn.commit()
        conn.close()

    def parse_excel_file(self, file_path, replace=True):
        try:
            logger.info(f"Parsing file: {file_path}, replace={replace}")
            workbook = openpyxl.load_workbook(file_path)
            sheet = workbook.active

            headers = [cell.value for cell in sheet[1]]
            logger.info(f"Headers: {headers}")

            required = ['Тема', 'Вопрос', 'Подсказка', 'Ответ']
            for col in required:
                if col not in headers:
                    raise ValueError(f"Отсутствует колонка: {col}")
            idx = {col: headers.index(col) for col in required}

            conn = sqlite3.connect(self.db_path)
            c = conn.cursor()
            if replace:
                c.execute("DELETE FROM questions")
                c.execute("DELETE FROM topics")
                logger.info("Cleared DB")

            inserted_topics = set()
            inserted = 0
            skipped = 0

            for row_num in range(2, sheet.max_row + 1):
                row = [sheet.cell(row=row_num, column=i + 1).value for i in range(len(headers))]
                if not any(str(cell).strip() if cell else '' for cell in row):
                    continue

                topic = row[idx['Тема']]
                question = row[idx['Вопрос']]
                hint = row[idx['Подсказка']]
                answer = row[idx['Ответ']]

                if not (topic and question and answer):
                    skipped += 1
                    logger.warning(f"Пропущена строка {row_num}: не хватает данных")
                    continue

                topic = str(topic).strip()
                question = str(question).strip()
                hint = str(hint).strip() if hint else ""
                answer = str(answer).strip()

                c.execute("INSERT OR IGNORE INTO topics (name) VALUES (?)", (topic,))
                if topic not in inserted_topics:
                    inserted_topics.add(topic)
                    logger.info(f"Добавлена тема: {topic}")

                c.execute("SELECT id FROM topics WHERE name = ?", (topic,))
                topic_id = c.fetchone()[0]

                c.execute('''INSERT INTO questions (topic_id, question_text, hint, answer)
                             VALUES (?, ?, ?, ?)''', (topic_id, question, hint, answer))
                inserted += 1

            conn.commit()
            conn.close()
            logger.info(f"Загружено: {inserted} вопросов, {len(inserted_topics)} тем, пропущено: {skipped}")
            return True

        except Exception as e:
            logger.error(f"Ошибка парсинга Excel: {e}", exc_info=True)
            return False

    def clear_database(self):
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("DELETE FROM questions")
        c.execute("DELETE FROM topics")
        conn.commit()
        conn.close()

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
        c.execute('''SELECT q.id, q.question_text, q.hint, q.answer
                     FROM questions q
                     JOIN topics t ON q.topic_id = t.id
                     WHERE t.name = ?''', (topic_name,))
        questions = [{'id': r[0], 'text': r[1], 'hint': r[2], 'answer': r[3]} for r in c.fetchall()]
        conn.close()
        return questions

    # === Handlers ===

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
        await update.message.reply_text(
            f"Привет, {user.first_name}! Выберите тему:",
            reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True)
        )
        return CHOOSE_TOPIC

    async def choose_topic(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        topic = update.message.text
        user_id = update.effective_user.id
        questions = self.get_questions_for_topic(topic)
        if not questions:
            await update.message.reply_text("В этой теме нет вопросов.")
            return CHOOSE_TOPIC

        self.user_states[user_id] = {
            'topic': topic,
            'questions': questions,
            'index': 0
        }

        q = questions[0]
        await update.message.reply_text(
            f"📚 Тема: {topic}\n\n❓ {q['text']}\n\n"
            "Команды:\n/hint — подсказка\n/answer — ответ\n/next — следующий",
            reply_markup=ReplyKeyboardMarkup([['/hint', '/answer', '/next']], one_time_keyboard=True)
        )
        return QUESTION

    async def show_hint(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        state = self.user_states.get(user_id)
        if not state or state['index'] >= len(state['questions']):
            await update.message.reply_text("Нет активного вопроса.")
            return CHOOSE_TOPIC

        q = state['questions'][state['index']]
        await update.message.reply_text(
            f"💡 Подсказка: {q['hint']}\n\n/answer — ответ\n/next — следующий",
            reply_markup=ReplyKeyboardMarkup([['/answer', '/next']], one_time_keyboard=True)
        )
        return HINT

    async def show_answer(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        state = self.user_states.get(user_id)
        if not state or state['index'] >= len(state['questions']):
            await update.message.reply_text("Нет активного вопроса.")
            return CHOOSE_TOPIC

        q = state['questions'][state['index']]
        await update.message.reply_text(
            f"✅ Ответ: {q['answer']}\n\n/next — следующий вопрос",
            reply_markup=ReplyKeyboardMarkup([['/next']], one_time_keyboard=True)
        )
        return ANSWER

    async def next_question(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        state = self.user_states.get(user_id)
        if not state:
            await update.message.reply_text("Сначала выберите тему.")
            return CHOOSE_TOPIC

        state['index'] += 1
        if state['index'] >= len(state['questions']):
            await update.message.reply_text("🎉 Вопросы закончились!")
            del self.user_states[user_id]
            return ConversationHandler.END

        q = state['questions'][state['index']]
        await update.message.reply_text(
            f"📚 Тема: {state['topic']}\n\n❓ {q['text']}\n\n"
            "Команды:\n/hint — подсказка\n/answer — ответ\n/next — следующий",
            reply_markup=ReplyKeyboardMarkup([['/hint', '/answer', '/next']], one_time_keyboard=True)
        )
        return QUESTION

    async def cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        self.user_states.pop(update.effective_user.id, None)
        await update.message.reply_text("Операция отменена.", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END

    # === Admin handlers ===

    async def admin_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id not in self.admin_ids:
            await update.message.reply_text("❌ Доступ запрещён.")
            return ConversationHandler.END

        keyboard = [
            ['📁 Загрузить данные', '📥 Дополнить данные'],
            ['🧹 Очистить базу', '↩️ Выйти']
        ]
        await update.message.reply_text(
            "🛡️ Админ-панель:\n"
            "• 📁 — заменить все данные\n"
            "• 📥 — добавить к существующим\n"
            "• 🧹 — удалить всё",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        )
        return ADMIN_MENU

    async def admin_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id not in self.admin_ids:
            await update.message.reply_text("❌ Доступ запрещён.", reply_markup=ReplyKeyboardRemove())
            return ConversationHandler.END

        choice = update.message.text
        if choice == "↩️ Выйти":
            await update.message.reply_text("Вы вышли.", reply_markup=ReplyKeyboardRemove())
            return ConversationHandler.END
        elif choice == "📁 Загрузить данные":
            await update.message.reply_text("Отправьте XLSX файл для замены данных.", reply_markup=ReplyKeyboardMarkup([['↩️ Отмена']]))
            return ADMIN_UPLOAD_REPLACE
        elif choice == "📥 Дополнить данные":
            await update.message.reply_text("Отправьте XLSX файл для добавления данных.", reply_markup=ReplyKeyboardMarkup([['↩️ Отмена']]))
            return ADMIN_UPLOAD_APPEND
        elif choice == "🧹 Очистить базу":
            await update.message.reply_text("Точно очистить? (да/нет)", reply_markup=ReplyKeyboardMarkup([['✅ Да', '❌ Нет']]))
            return ADMIN_CONFIRM_CLEAR
        else:
            await update.message.reply_text("Выберите действие из меню.")
            return ADMIN_MENU

    async def admin_upload_file(self, update: Update, context: ContextTypes.DEFAULT_TYPE, replace=True):
        if update.message.text == "↩️ Отмена":
            return await self.admin_menu(update, context)

        if not update.message.document:
            await update.message.reply_text("Отправьте XLSX файл.")
            return ADMIN_UPLOAD_REPLACE if replace else ADMIN_UPLOAD_APPEND

        doc = update.message.document
        if not doc.file_name.endswith(('.xlsx', '.xls')):
            await update.message.reply_text("Только XLS/XLSX файлы.")
            return ADMIN_UPLOAD_REPLACE if replace else ADMIN_UPLOAD_APPEND

        file = await doc.get_file()
        with tempfile.NamedTemporaryFile(delete=False, suffix='.xlsx') as tmp:
            await file.download_to_drive(tmp.name)
            success = self.parse_excel_file(tmp.name, replace=replace)
            os.unlink(tmp.name)

        if success:
            await update.message.reply_text("✅ Данные успешно загружены!", reply_markup=ReplyKeyboardRemove())
            return ConversationHandler.END
        else:
            await update.message.reply_text("❌ Ошибка при загрузке файла.", reply_markup=ReplyKeyboardMarkup([['↩️ Отмена']]))
            return ADMIN_UPLOAD_REPLACE if replace else ADMIN_UPLOAD_APPEND

    async def admin_confirm_clear(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.message.text == "✅ Да":
            self.clear_database()
            await update.message.reply_text("🧹 База очищена.", reply_markup=ReplyKeyboardRemove())
            return ConversationHandler.END
        else:
            await update.message.reply_text("Очистка отменена.", reply_markup=ReplyKeyboardRemove())
            return ADMIN_MENU


# === Main ===

async def main():
    TOKEN = os.getenv("BOT_TOKEN")
    if not TOKEN:
        raise ValueError("BOT_TOKEN не задан в secrets!")

    admin_ids_str = os.getenv("ADMIN_IDS", "")
    admin_ids = [int(x.strip()) for x in admin_ids_str.split(",") if x.strip().isdigit()]
    if not admin_ids:
        logger.warning("ADMIN_IDS не задан — админка недоступна")

    quiz_bot = QuizBot(admin_ids=admin_ids)
    persistence = PicklePersistence(filepath="conversation_states.pkl")
    app = Application.builder().token(TOKEN).persistence(persistence).build()

    # Основной диалог
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', quiz_bot.start)],
        states={
            CHOOSE_TOPIC: [MessageHandler(filters.TEXT & ~filters.COMMAND, quiz_bot.choose_topic)],
            QUESTION: [
                CommandHandler('hint', quiz_bot.show_hint),
                CommandHandler('answer', quiz_bot.show_answer),
                CommandHandler('next', quiz_bot.next_question),
            ],
            HINT: [
                CommandHandler('answer', quiz_bot.show_answer),
                CommandHandler('next', quiz_bot.next_question),
            ],
            ANSWER: [CommandHandler('next', quiz_bot.next_question)],
        },
        fallbacks=[CommandHandler('cancel', quiz_bot.cancel)],
        name="quiz_conv",
        persistent=True
    )

    # Админка
    admin_handler = ConversationHandler(
        entry_points=[CommandHandler('admin', quiz_bot.admin_start)],
        states={
            ADMIN_MENU: [MessageHandler(filters.TEXT & ~filters.COMMAND, quiz_bot.admin_menu)],
            ADMIN_UPLOAD_REPLACE: [
                MessageHandler(filters.Document.ALL, quiz_bot.admin_upload_file),
                MessageHandler(filters.TEXT & ~filters.COMMAND, quiz_bot.admin_upload_file)
            ],
            ADMIN_UPLOAD_APPEND: [
                MessageHandler(filters.Document.ALL, lambda u, c: quiz_bot.admin_upload_file(u, c, replace=False)),
                MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: quiz_bot.admin_upload_file(u, c, replace=False))
            ],
            ADMIN_CONFIRM_CLEAR: [MessageHandler(filters.TEXT & ~filters.COMMAND, quiz_bot.admin_confirm_clear)]
        },
        fallbacks=[CommandHandler('cancel', quiz_bot.cancel)],
        name="admin_conv",
        persistent=True
    )

    app.add_handler(conv_handler)
    app.add_handler(admin_handler)

    logger.info("Бот запускается в режиме polling...")
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    logger.info("Бот работает. Нажмите Ctrl+C для остановки.")

    try:
        while True:
            await asyncio.sleep(3600)
    except KeyboardInterrupt:
        logger.info("Остановка бота...")
    finally:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()


if __name__ == '__main__':
    import asyncio
    asyncio.run(main())