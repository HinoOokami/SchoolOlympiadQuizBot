import os
import logging
import sqlite3
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application, CommandHandler, MessageHandler, 
    ContextTypes, ConversationHandler, filters, PicklePersistence
)
import tempfile
import openpyxl

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Состояния для ConversationHandler
(UPLOAD, CHOOSE_TOPIC, QUESTION, HINT, ANSWER, 
 ADMIN_MENU, ADMIN_UPLOAD, ADMIN_CONFIRM_CLEAR) = range(8)

class QuizBot:
    def __init__(self, admin_ids):
        self.db_path = 'quiz_bot.db'
        self.admin_ids = admin_ids  # Список ID администраторов
        self.init_database()
        self.user_states = {}

    def init_database(self):
        """Инициализация базы данных SQLite"""
        conn = sqlite3.connect(self.db_path)
        # Включаем поддержку внешних ключей
        conn.execute("PRAGMA foreign_keys = ON")
        c = conn.cursor()
        
        # Таблица для тем
        c.execute('''CREATE TABLE IF NOT EXISTS topics
                     (id INTEGER PRIMARY KEY AUTOINCREMENT,
                      name TEXT UNIQUE)''')
        
        # Таблица для вопросов
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
        """Сохранение пользователя в базу данных"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        
        # Создаем таблицу пользователей если её нет
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
        """
        Парсинг Excel файла с использованием openpyxl
        replace: True - заменить все данные, False - добавить новые
        """
        try:
            workbook = openpyxl.load_workbook(file_path)
            sheet = workbook.active
            
            # Читаем заголовки
            headers = []
            for cell in sheet[1]:
                headers.append(cell.value)
            
            # Проверяем необходимые колонки
            required_headers = ['Тема', 'Вопрос', 'Подсказка', 'Ответ']
            for req in required_headers:
                if req not in headers:
                    raise ValueError(f"Отсутствует колонка: {req}")
            
            conn = sqlite3.connect(self.db_path)
            c = conn.cursor()
            
            # Очищаем данные только если требуется замена
            if replace:
                c.execute("DELETE FROM questions")
                c.execute("DELETE FROM topics")
            
            # Читаем данные построчно
            for row_num in range(2, sheet.max_row + 1):
                row_data = []
                for col_num in range(1, len(headers) + 1):
                    cell_value = sheet.cell(row=row_num, column=col_num).value
                    row_data.append(cell_value)
                
                # Пропускаем пустые строки
                if not any(row_data):
                    continue
                
                # Проверка количества колонок
                if len(row_data) < len(required_headers):
                    logger.warning(f"Пропущена строка {row_num}: недостаточно колонок")
                    continue
                
                # Извлекаем данные по заголовкам
                topic_name = row_data[headers.index('Тема')]
                question_text = row_data[headers.index('Вопрос')]
                hint = row_data[headers.index('Подсказка')]
                answer = row_data[headers.index('Ответ')]
                
                # Проверка обязательных полей
                if not all([topic_name, question_text, answer]):
                    logger.warning(f"Пропущена строка {row_num}: отсутствуют обязательные поля")
                    continue
                
                # Обрабатываем сложность (если есть)
                difficulty = 'medium'
                if 'Сложность' in headers:
                    difficulty = row_data[headers.index('Сложность')] or 'medium'
                
                # Пропускаем пустые темы
                if not str(topic_name).strip():
                    logger.warning(f"Пропущена строка {row_num}: пустая тема")
                    continue
                
                # Сохраняем тему (игнорируем дубликаты при добавлении)
                c.execute("INSERT OR IGNORE INTO topics (name) VALUES (?)", (topic_name,))
                c.execute("SELECT id FROM topics WHERE name = ?", (topic_name,))
                topic_id = c.fetchone()[0]
                
                # Сохраняем вопрос (дубликаты возможны, но это допустимо)
                c.execute('''INSERT INTO questions (topic_id, question_text, hint, answer, difficulty)
                             VALUES (?, ?, ?, ?, ?)''',
                         (topic_id, question_text, hint, answer, difficulty))
            
            conn.commit()
            conn.close()
            return True
            
        except Exception as e:
            logger.error(f"Error parsing Excel file: {e}")
            return False

    def clear_database(self):
        """Очистка базы данных"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("DELETE FROM questions")
        c.execute("DELETE FROM topics")
        conn.commit()
        conn.close()

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /start"""
        user = update.effective_user
        self.save_user_to_db(user)
        
        await update.message.reply_text(
            f"Привет, {user.first_name}! Я бот для викторин.\n\n"
            "Отправьте мне XLS/XLSX файл с вопросами.\n\n"
            "Формат файла должен содержать колонки:\n"
            "• Тема\n• Вопрос\n• Подсказка\n• Ответ\n• Сложность (опционально)"
        )
        return UPLOAD

    async def upload_file(self, update: Update, context: ContextTypes.DEFAULT_TYPE, replace=True):
        """Обработчик загрузки файла (общий для основного и админского режима)"""
        if update.message.document:
            file = await update.message.document.get_file()
            file_extension = os.path.splitext(update.message.document.file_name)[1].lower()
            
            if file_extension not in ['.xls', '.xlsx']:
                await update.message.reply_text("Пожалуйста, отправьте файл в формате XLS или XLSX.")
                return UPLOAD if replace else ADMIN_UPLOAD
            
            with tempfile.NamedTemporaryFile(delete=False, suffix=file_extension) as tmp_file:
                await file.download_to_drive(tmp_file.name)
                
                success = self.parse_excel_file(tmp_file.name, replace=replace)
                os.unlink(tmp_file.name)
                
                if success:
                    topics = self.get_topics_from_db()
                    if not topics:
                        await update.message.reply_text(
                            "Файл загружен, но темы не найдены. Проверьте содержимое файла."
                        )
                        return UPLOAD if replace else ADMIN_UPLOAD
                    
                    if replace:
                        keyboard = [[topic] for topic in topics]
                        reply_markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True)
                        await update.message.reply_text(
                            "Файл успешно загружен! Выберите тему:",
                            reply_markup=reply_markup
                        )
                        return CHOOSE_TOPIC
                    else:
                        await update.message.reply_text(
                            "Данные успешно добавлены в базу!",
                            reply_markup=ReplyKeyboardRemove()
                        )
                        return ADMIN_MENU
                else:
                    await update.message.reply_text("Ошибка при чтении Excel-файла. Проверьте формат.")
                    return UPLOAD if replace else ADMIN_UPLOAD
        else:
            await update.message.reply_text("Пожалуйста, отправьте XLS/XLSX файл.")
            return UPLOAD if replace else ADMIN_UPLOAD

    def get_topics_from_db(self):
        """Получение списка тем из базы данных"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        
        c.execute("SELECT name FROM topics ORDER BY name")
        topics = [row[0] for row in c.fetchall()]
        
        conn.close()
        return topics

    def get_questions_for_topic(self, topic_name):
        """Получение вопросов для выбранной темы из базы данных (без учета регистра)"""
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
        """Обработчик выбора темы"""
        topic_name = update.message.text
        user_id = update.effective_user.id
        
        # Сохраняем выбранную тему для пользователя
        if user_id not in self.user_states:
            self.user_states[user_id] = {}
        
        self.user_states[user_id]['current_topic'] = topic_name
        questions = self.get_questions_for_topic(topic_name)
        self.user_states[user_id]['questions'] = questions
        self.user_states[user_id]['current_question_index'] = 0
        
        if questions:
            # Показываем первый вопрос
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
            return QUESTION
        else:
            await update.message.reply_text("В этой теме нет вопросов.")
            return CHOOSE_TOPIC

    async def show_question(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Показать текущий вопрос"""
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
        """Показать подсказку"""
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
                return HINT
        await update.message.reply_text("Ошибка при получении подсказки. Вернитесь к вопросу.", 
                                      reply_markup=ReplyKeyboardMarkup([['/hint', '/answer', '/next']], one_time_keyboard=True))
        return QUESTION

    async def show_answer(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Показать ответ"""
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
                return ANSWER
        await update.message.reply_text("Ошибка при получении ответа. Вернитесь к вопросу.", 
                                      reply_markup=ReplyKeyboardMarkup([['/hint', '/answer', '/next']], one_time_keyboard=True))
        return QUESTION

    async def next_question(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Следующий вопрос"""
        user_id = update.effective_user.id
        if user_id in self.user_states:
            self.user_states[user_id]['current_question_index'] += 1
            return await self.show_question(update, context)
        return CHOOSE_TOPIC

    async def cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Отмена операции"""
        self.user_states.pop(update.effective_user.id, None)
        await update.message.reply_text("Операция отменена.", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END

    # === Админские функции ===
    async def admin_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /admin"""
        user_id = update.effective_user.id
        if user_id not in self.admin_ids:
            await update.message.reply_text("❌ Доступ запрещен. Вы не администратор.")
            return ConversationHandler.END
        
        keyboard = [
            ['📁 Загрузить данные', '🧹 Очистить базу'],
            ['↩️ Выйти из админ-режима']
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=False, resize_keyboard=True)
        await update.message.reply_text(
            "🛡️ Добро пожаловать в админ-панель!\n\n"
            "Выберите действие:",
            reply_markup=reply_markup
        )
        return ADMIN_MENU

    async def admin_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик меню администратора"""
        choice = update.message.text
        user_id = update.effective_user.id
        
        if user_id not in self.admin_ids:
            return ConversationHandler.END
        
        if choice == "↩️ Выйти из админ-режима":
            await update.message.reply_text(
                "✅ Вы вышли из админ-режима",
                reply_markup=ReplyKeyboardRemove()
            )
            return ConversationHandler.END
        
        elif choice == "📁 Загрузить данные":
            await update.message.reply_text(
                "📥 Отправьте XLS/XLSX файл для ДОПОЛНЕНИЯ базы данных\n\n"
                "Формат файла должен содержать колонки:\n"
                "• Тема\n• Вопрос\n• Подсказка\n• Ответ\n• Сложность (опционально)",
                reply_markup=ReplyKeyboardMarkup([['↩️ Отмена']], one_time_keyboard=True)
            )
            return ADMIN_UPLOAD
        
        elif choice == "🧹 Очистить базу":
            keyboard = [['✅ Да, очистить', '❌ Нет, отмена']]
            reply_markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True)
            await update.message.reply_text(
                "⚠️ ВНИМАНИЕ! Эта операция удалит ВСЕ данные из базы.\n"
                "Вы уверены, что хотите продолжить?",
                reply_markup=reply_markup
            )
            return ADMIN_CONFIRM_CLEAR
        
        return ADMIN_MENU

    async def admin_upload_file(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик загрузки файла в админском режиме (добавление данных)"""
        if update.message.text == "↩️ Отмена":
            return await self.admin_menu(update, context)
        
        return await self.upload_file(update, context, replace=False)

    async def admin_confirm_clear(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Подтверждение очистки базы"""
        if update.message.text == "✅ Да, очистить":
            self.clear_database()
            await update.message.reply_text(
                "🧹 База данных успешно очищена!",
                reply_markup=ReplyKeyboardRemove()
            )
        return ADMIN_MENU


def main():
    """Основная функция"""
    # Настройка администраторов (замените на реальные ID)
    ADMIN_IDS = [123456789, 987654321]  # Пример: ваш Telegram ID
    
    # Загрузка токена из переменной окружения (рекомендуется)
    TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    if not TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN не установлен!")
    
    quiz_bot = QuizBot(admin_ids=ADMIN_IDS)
    
    # Создаем Application с сохранением состояний
    persistence = PicklePersistence(filepath="quiz_conversation_states.pkl")
    application = Application.builder().token(TOKEN).persistence(persistence).build()
    
    # Создаем ConversationHandler для основного режима
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', quiz_bot.start)],
        states={
            UPLOAD: [
                MessageHandler(filters.Document.ALL, quiz_bot.upload_file),
                MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: u.message.reply_text(
                    "Пожалуйста, отправьте XLS/XLSX файл с вопросами."
                ))
            ],
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
    
    # Создаем ConversationHandler для админского режима
    admin_handler = ConversationHandler(
        entry_points=[CommandHandler('admin', quiz_bot.admin_start)],
        states={
            ADMIN_MENU: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, quiz_bot.admin_menu)
            ],
            ADMIN_UPLOAD: [
                MessageHandler(filters.Document.ALL, quiz_bot.admin_upload_file),
                MessageHandler(filters.TEXT & ~filters.COMMAND, quiz_bot.admin_upload_file)
            ],
            ADMIN_CONFIRM_CLEAR: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, quiz_bot.admin_confirm_clear)
            ]
        },
        fallbacks=[CommandHandler('cancel', quiz_bot.cancel)],
        name="admin_conversation",
        persistent=True
    )
    
    # Добавляем обработчики
    application.add_handler(conv_handler)
    application.add_handler(admin_handler)
    
    # Запускаем бота
    logger.info("Бот запущен...")
    application.run_polling()

if __name__ == '__main__':
    main()