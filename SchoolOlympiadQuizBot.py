import os
import logging
import sqlite3
import tempfile
import zipfile
import shutil
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    ContextTypes, ConversationHandler, filters, PicklePersistence
)
import openpyxl
import re

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Состояния
(CHOOSE_YEAR, CHOOSE_EXERCISE, QUESTION, HINT, ANSWER,
 ADMIN_MENU, ADMIN_UPLOAD_REPLACE, ADMIN_UPLOAD_APPEND, ADMIN_CONFIRM_CLEAR) = range(9)

# Папка для изображений
IMAGE_DIR = "images"
os.makedirs(IMAGE_DIR, exist_ok=True)


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
        c.execute('''CREATE TABLE IF NOT EXISTS years
                     (id INTEGER PRIMARY KEY AUTOINCREMENT, year INTEGER UNIQUE)''')
        c.execute('''CREATE TABLE IF NOT EXISTS topics
                     (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE)''')
        c.execute('''CREATE TABLE IF NOT EXISTS olympiads
                     (id INTEGER PRIMARY KEY AUTOINCREMENT,
                      year_id INTEGER,
                      excercise INTEGER,
                      topic_id INTEGER,
                      task TEXT,
                      task_picture TEXT,
                      hint TEXT,
                      hint_picture TEXT,
                      answer TEXT,
                      answer_picture TEXT,
                      FOREIGN KEY (year_id) REFERENCES years (id) ON DELETE CASCADE,
                      FOREIGN KEY (topic_id) REFERENCES topics (id) ON DELETE CASCADE,
                      UNIQUE(year_id, topic_id, task, task_picture))''')
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

    def parse_excel_and_images(self, excel_path, image_dir, replace=True):
        try:
            logger.info(f"Parsing Excel: {excel_path}, replace={replace}")
            workbook = openpyxl.load_workbook(excel_path)
            sheet = workbook.active

            if sheet.max_row < 2:
                logger.warning("Excel file is empty")
                return False

            headers = [cell.value for cell in sheet[1]]
            logger.info(f"Headers: {headers}")

            required = ['Year', 'Excercise', 'Topic', 'Task', 'Hint', 'Answer']
            for col in required:
                if col not in headers:
                    raise ValueError(f"Missing column: {col}")
            idx = {col: headers.index(col) for col in required}

            # Optional picture columns
            pic_cols = {}
            for col in ['Task_picture', 'Hint_picture', 'Answer_picture']:
                if col in headers:
                    pic_cols[col] = headers.index(col)

            conn = sqlite3.connect(self.db_path)
            c = conn.cursor()
            if replace:
                c.execute("DELETE FROM olympiads")
                c.execute("DELETE FROM topics")
                c.execute("DELETE FROM years")
                logger.info("Cleared DB")

            inserted_years = set()
            inserted_topics = set()
            inserted = 0
            skipped = 0

            for row_num in range(2, sheet.max_row + 1):
                row = [sheet.cell(row=row_num, column=i + 1).value for i in range(len(headers))]
                if not any(str(cell).strip() if cell else '' for cell in row):
                    continue

                try:
                    year = int(float(row[idx['Year']]))
                except (ValueError, TypeError):
                    skipped += 1
                    logger.warning(f"Invalid year in row {row_num}")
                    continue

                try:
                    excercise = int(float(row[idx['Excercise']]))
                except (ValueError, TypeError):
                    skipped += 1
                    logger.warning(f"Invalid excercise in row {row_num}")
                    continue

                topic = row[idx['Topic']]
                task = row[idx['Task']]
                task_picture = row[idx['Task_picture']]
                hint = row[idx['Hint']]
                hint_picture = row[idx['Hint_picture']]
                answer = row[idx['Answer']]
                answer_picture = row[idx['Answer_picture']]

                if not (year and excercise and topic and (task or task_picture) and (hint or hint_picture) and (answer or answer_picture)):
                    skipped += 1
                    logger.warning(f"Missing data in row {row_num}")
                    continue

                topic = str(topic).strip()
                task = str(task).strip()
                hint = str(hint).strip() if hint else ""
                answer = str(answer).strip()

                # Get picture filenames
                t_pic = str(row[pic_cols['Task_picture']]).strip() if 'Task_picture' in pic_cols and row[pic_cols['Task_picture']] else None
                h_pic = str(row[pic_cols['Hint_picture']]).strip() if 'Hint_picture' in pic_cols and row[pic_cols['Hint_picture']] else None
                a_pic = str(row[pic_cols['Answer_picture']]).strip() if 'Answer_picture' in pic_cols and row[pic_cols['Answer_picture']] else None

                # Validate picture files exist
                for pic in [t_pic, h_pic, a_pic]:
                    if pic and not os.path.exists(os.path.join(image_dir, pic)):
                        logger.warning(f"Picture file not found: {pic}")
                        # Optionally skip row or set to None
                        # Here we keep filename but it may fail later

                c.execute("INSERT OR IGNORE INTO years (year) VALUES (?)", (year,))
                if year not in inserted_years:
                    inserted_years.add(year)
                    logger.info(f"Added year: {year}")

                c.execute("INSERT OR IGNORE INTO topics (name) VALUES (?)", (topic,))
                if topic not in inserted_topics:
                    inserted_topics.add(topic)
                    logger.info(f"Added topic: {topic}")

                c.execute("SELECT id FROM years WHERE year = ?", (year,))
                year_id = c.fetchone()[0]

                c.execute("SELECT id FROM topics WHERE name = ?", (topic,))
                topic_id = c.fetchone()[0]

                c.execute('''INSERT OR REPLACE INTO olympiads 
                             (year_id, excercise, topic_id, task, task_picture, 
                              hint, hint_picture, answer, answer_picture)
                             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                          (year_id, excercise, topic_id, task, t_pic, hint, h_pic, answer, a_pic))
                inserted += 1

            conn.commit()
            conn.close()
            logger.info(f"Loaded: {inserted} excercises, {len(inserted_topics)} topics, {len(inserted_years)} years, skipped: {skipped}")
            return True

        except Exception as e:
            logger.error(f"Error parsing Excel: {e}", exc_info=True)
            return False

    def clear_database(self):
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("DELETE FROM olympiads")
        c.execute("DELETE FROM topics")
        c.execute("DELETE FROM years")
        conn.commit()
        conn.close()

    def clear_images(self):
        # Удаляем все файлы изображений
        if os.path.exists(IMAGE_DIR):
            for filename in os.listdir(IMAGE_DIR):
                file_path = os.path.join(IMAGE_DIR, filename)
                try:
                    if os.path.isfile(file_path):
                        os.unlink(file_path)
                        logger.info(f"Deleted image file: {filename}")
                except Exception as e:
                    logger.error(f"Failed to delete {file_path}: {e}")
        logger.info("All image files deleted.")

    def get_years_from_db(self):
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("SELECT year FROM years ORDER BY year")
        years = [row[0] for row in c.fetchall()]
        conn.close()
        return years

    def get_questions_for_year(self, year):
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute('''SELECT o.id, o.excercise, o.task, o.task_picture,
                            o.hint, o.hint_picture,
                            o.answer, o.answer_picture
                     FROM olympiads o
                     JOIN years y ON o.year_id = y.id
                     WHERE y.year = ?''', (year,))
        tasks = []
        for r in c.fetchall():
            tasks.append({
                'id': r[0],
                'excercise': r[1],
                'task': r[2],
                't_pic': r[3],
                'hint': r[4],
                'h_pic': r[5],
                'answer': r[6],
                'a_pic': r[7]
            })
        conn.close()
        return tasks

    def get_exercises_for_year(self, year):
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute('''SELECT o.excersise
                     FROM olympiads o
                     JOIN years y ON o.year_id = y.id
                     WHERE y.year = ?
                     ORDER BY o.excersise''', (year,))
        exercises = [{'excersise': row[0]} for row in c.fetchall()]
        conn.close()
        return exercises   

    def get_tasks_for_year_and_exercise(self, year, excersise):
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute('''SELECT o.id, o.excersise, o.task, o.task_picture,
                            o.hint, o.hint_picture,
                            o.answer, o.answer_picture
                     FROM olympiads o
                     JOIN years y ON o.year_id = y.id
                     WHERE y.year = ? AND o.excersise = ?''', (year, excersise))
        tasks = []
        for r in c.fetchall():
            tasks.append({
                'id': r[0],
                'excercise': r[1],
                'task': r[2],
                't_pic': r[3],
                'hint': r[4],
                'h_pic': r[5],
                'answer': r[6],
                'a_pic': r[7]
            })
        conn.close()
        return tasks
    
    # === Handlers ===

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        self.save_user_to_db(user)
        years = self.get_years_from_db()
        if not years:
            await update.message.reply_text(
                f"Привет, {user.first_name}! Я бот для викторин.\n\n"
                "Нет доступных годов. Обратитесь к админу."
            )
            return ConversationHandler.END

        keyboard = [[str(year)] for year in years]
        await update.message.reply_text(
            f"Привет, {user.first_name}! Выберите год:",
            reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=False)
        )
        return CHOOSE_YEAR

    async def choose_year(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            year = int(update.message.text)
        except ValueError:
            years = self.get_years_from_db()
            keyboard = [[str(y)] for y in years]
            await update.message.reply_text(
                "Выберите год из списка.",
                reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=False)
            )
            return CHOOSE_YEAR

        user_id = update.effective_user.id
        exercises = self.get_exercises_for_year(year)
        if not exercises:
            await update.message.reply_text("В этом году нет заданий.")
            return CHOOSE_YEAR

        # Формируем кнопки: "2011 задание 1", "2011 задание 2", ...
        keyboard = [[f"{year} задание {ex['excersise']}"] for ex in exercises]
        await update.message.reply_text(
            f"Выберите задание для {year} года:",
            reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True)
        )

        # Сохраняем год и список заданий
        self.user_states[user_id] = {
            'year': year,
            'exercises': exercises,
        }
        return CHOOSE_EXERCISE

    async def choose_exercise(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        state = self.user_states.get(user_id)
        if not state:
            await update.message.reply_text("Сначала выберите год.")
            return CHOOSE_YEAR

        year = state['year']
        text = update.message.text

        # Парсим "2011 задание 3" → excersise = 3
        try:
            if f"{year} задание " in text:
                excersise = int(text.split()[-1])
            else:
                raise ValueError("Неверный формат")
        except (ValueError, IndexError):
            await update.message.reply_text("Пожалуйста, выберите задание из списка.")
            return CHOOSE_EXERCISE

        # Получаем задачу (первую, если их несколько с таким годом и номером)
        tasks = self.get_tasks_for_year_and_exercise(year, excersise)
        if not tasks:
            await update.message.reply_text("Задание не найдено.")
            return CHOOSE_EXERCISE

        self.user_states[user_id] = {
            'year': str(year),
            'tasks': tasks,
            'index': 0
        }

        await self._send_task(update, tasks[0])
        return QUESTION

    async def _send_task(self, update: Update, q):
        if q['task']:
            await update.message.reply_text(f"❓ {q['task']}")
        if q['t_pic']:
            pic_path = os.path.join(IMAGE_DIR, q['t_pic'])
            if os.path.exists(pic_path):
                await update.message.reply_photo(photo=pic_path)
            else:
                await update.message.reply_text(f"🖼️ Изображение задачи не найдено: {q['t_pic']}")

        await update.message.reply_text(
            "Команды:\n/hint — подсказка\n/answer — ответ\n/next — следующий",
            reply_markup=ReplyKeyboardMarkup([['Подсказка', 'Ответ', 'Следующий']], one_time_keyboard=True)
        )

    async def show_hint(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        state = self.user_states.get(user_id)
        if not state or state['index'] >= len(state['tasks']):
            await update.message.reply_text("Нет активной задачи.")
            return CHOOSE_YEAR

        q = state['tasks'][state['index']]
        if q['hint']:
            await update.message.reply_text(f"💡 Подсказка: {q['hint']}")
        if q['h_pic']:
            pic_path = os.path.join(IMAGE_DIR, q['h_pic'])
            if os.path.exists(pic_path):
                await update.message.reply_photo(photo=pic_path)
            else:
                await update.message.reply_text(f"🖼️ Изображение подсказки не найдено: {q['h_pic']}")

        await update.message.reply_text(
            "Команды:\n/answer — ответ\n/next — следующий",
            reply_markup=ReplyKeyboardMarkup([['Ответ', 'Следующий']], one_time_keyboard=True)
        )
        return HINT

    async def show_answer(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        state = self.user_states.get(user_id)
        if not state or state['index'] >= len(state['tasks']):
            await update.message.reply_text("Нет активной задачи.")
            return CHOOSE_YEAR

        q = state['tasks'][state['index']]
        if q['answer']:
            await update.message.reply_text(f"✅ Ответ: {q['answer']}")
        if q['a_pic']:
            pic_path = os.path.join(IMAGE_DIR, q['a_pic'])
            if os.path.exists(pic_path):
                await update.message.reply_photo(photo=pic_path)
            else:
                await update.message.reply_text(f"🖼️ Изображение ответа не найдено: {q['a_pic']}")

        await update.message.reply_text(
            "/next — следующая задача",
            reply_markup=ReplyKeyboardMarkup([['Следующий']], one_time_keyboard=True)
        )
        return ANSWER

    async def next_question(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        state = self.user_states.get(user_id)
        if not state:
            await update.message.reply_text("Сначала выберите год.")
            return CHOOSE_YEAR

        state['index'] += 1
        if state['index'] >= len(state['tasks']):
            await update.message.reply_text("🎉 Вопросы закончились!")
            del self.user_states[user_id]
            return ConversationHandler.END

        await self._send_task(update, state['tasks'][state['index']])
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
            await update.message.reply_text("Отправьте ZIP-архив с Excel и изображениями.")
            return ADMIN_UPLOAD_REPLACE
        elif choice == "📥 Дополнить данные":
            await update.message.reply_text("Отправьте ZIP-архив с Excel и изображениями для дополнения.")
            return ADMIN_UPLOAD_APPEND
        elif choice == "🧹 Очистить базу":
            await update.message.reply_text("Точно очистить?", reply_markup=ReplyKeyboardMarkup([['✅ Да', '❌ Нет']]))
            return ADMIN_CONFIRM_CLEAR
        else:
            await update.message.reply_text("Выберите действие из меню.")
            return ADMIN_MENU

    async def admin_upload_file(self, update: Update, context: ContextTypes.DEFAULT_TYPE, replace=True):
        if not update.message.document or not update.message.document.file_name.endswith('.zip'):
            await update.message.reply_text("Отправьте ZIP-архив.")
            return ADMIN_UPLOAD_REPLACE if replace else ADMIN_UPLOAD_APPEND

        file = await update.message.document.get_file()
        with tempfile.TemporaryDirectory() as tmp_dir:
            zip_path = os.path.join(tmp_dir, "data.zip")
            await file.download_to_drive(zip_path)

            try:
                with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                    zip_ref.extractall(tmp_dir)
            except zipfile.BadZipFile:
                await update.message.reply_text("Неверный ZIP-файл.")
                return ADMIN_UPLOAD_REPLACE if replace else ADMIN_UPLOAD_APPEND

            # Find Excel file
            excel_files = [f for f in os.listdir(tmp_dir) if f.endswith(('.xlsx', '.xls'))]
            if not excel_files:
                await update.message.reply_text("В архиве нет Excel-файла.")
                return ADMIN_UPLOAD_REPLACE if replace else ADMIN_UPLOAD_APPEND

            excel_path = os.path.join(tmp_dir, excel_files[0])

            # Copy images to persistent dir
            for item in os.listdir(tmp_dir):
                if item.lower().endswith(('.jpg', '.jpeg', '.png')):
                    src = os.path.join(tmp_dir, item)
                    dst = os.path.join(IMAGE_DIR, item)
                    shutil.copy(src, dst)

            success = self.parse_excel_and_images(excel_path, tmp_dir, replace=replace)

        if success:
            await update.message.reply_text("✅ Данные успешно загружены!", reply_markup=ReplyKeyboardRemove())
            return ConversationHandler.END
        else:
            await update.message.reply_text("❌ Ошибка при загрузке данных.", reply_markup=ReplyKeyboardMarkup([['↩️ Отмена']]))
            return ADMIN_UPLOAD_REPLACE if replace else ADMIN_UPLOAD_APPEND

    async def admin_confirm_clear(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.message.text == "✅ Да":
            self.clear_database()
            self.clear_images()
            await update.message.reply_text("🧹 Данные очищены.", reply_markup=ReplyKeyboardRemove())
            return ConversationHandler.END
        else:
            await update.message.reply_text("Очистка отменена.", reply_markup=ReplyKeyboardRemove())
            return ADMIN_MENU


# === Main ===

async def main():
    TOKEN = os.getenv("BOT_TOKEN")
    if not TOKEN:
        raise ValueError("BOT_TOKEN не задан!")

    admin_ids_str = os.getenv("ADMIN_IDS", "")
    admin_ids = [int(x.strip()) for x in admin_ids_str.split(",") if x.strip().isdigit()]
    if not admin_ids:
        logger.warning("ADMIN_IDS не задан")

    quiz_bot = QuizBot(admin_ids=admin_ids)
    persistence = PicklePersistence(filepath="conversation_states.pkl")
    app = Application.builder().token(TOKEN).persistence(persistence).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', quiz_bot.start)],
        states={
            CHOOSE_YEAR: [MessageHandler(filters.TEXT & ~filters.COMMAND, quiz_bot.choose_year)],
            CHOOSE_EXERCISE: [MessageHandler(filters.TEXT & ~filters.COMMAND, quiz_bot.choose_exercise)],
            QUESTION: [
                CommandHandler('hint', quiz_bot.show_hint),
                CommandHandler('answer', quiz_bot.show_answer),
                CommandHandler('next', quiz_bot.next_question),
                MessageHandler(filters.Regex(re.compile(r"^(Подсказка|Hint)$", re.IGNORECASE)), quiz_bot.show_hint),
                MessageHandler(filters.Regex(re.compile(r"^(Ответ|Answer)$", re.IGNORECASE)), quiz_bot.show_answer),
                MessageHandler(filters.Regex(re.compile(r"^(Следующий|Next)$", re.IGNORECASE)), quiz_bot.next_question),
            ],
            HINT: [
                CommandHandler('answer', quiz_bot.show_answer),
                CommandHandler('next', quiz_bot.next_question),
                MessageHandler(filters.Regex(re.compile(r"^(Ответ|Answer)$", re.IGNORECASE)), quiz_bot.show_answer),
                MessageHandler(filters.Regex(re.compile(r"^(Следующий|Next)$", re.IGNORECASE)), quiz_bot.next_question),
            ],
            ANSWER: [
                CommandHandler('next', quiz_bot.next_question),
                MessageHandler(filters.Regex(re.compile(r"^(Следующий|Next)$", re.IGNORECASE)), quiz_bot.next_question),
            ],
        },
        fallbacks=[
            CommandHandler('cancel', quiz_bot.cancel),
            CommandHandler('start', quiz_bot.start),
            MessageHandler(filters.Regex(re.compile(r"^(Отмена|Cancel)$", re.IGNORECASE)), quiz_bot.cancel),
            MessageHandler(filters.Regex(re.compile(r"^(Начать|Start)$", re.IGNORECASE)), quiz_bot.start),
        ],
        name="main_conversation",
        persistent=True
    )

    admin_handler = ConversationHandler(
        entry_points=[CommandHandler('admin', quiz_bot.admin_start)],
        states={
            ADMIN_MENU: [MessageHandler(filters.TEXT & ~filters.COMMAND, quiz_bot.admin_menu)],
            ADMIN_UPLOAD_REPLACE: [
                MessageHandler(filters.Document.ZIP, quiz_bot.admin_upload_file),
                MessageHandler(filters.TEXT & ~filters.COMMAND, quiz_bot.admin_upload_file)
            ],
            ADMIN_UPLOAD_APPEND: [
                MessageHandler(filters.Document.ZIP, lambda u, c: quiz_bot.admin_upload_file(u, c, replace=False)),
                MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: quiz_bot.admin_upload_file(u, c, replace=False))
            ],
            ADMIN_CONFIRM_CLEAR: [MessageHandler(filters.TEXT & ~filters.COMMAND, quiz_bot.admin_confirm_clear)]
        },
        fallbacks=[
            CommandHandler('cancel', quiz_bot.cancel),
            MessageHandler(filters.Regex("^(Отмена|Cancel)$"), quiz_bot.cancel),
        ],
        name="admin_conv",
        persistent=True
    )

    app.add_handler(conv_handler)
    app.add_handler(admin_handler)

    logger.info("Запуск бота в режиме polling...")
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    logger.info("Бот работает.")

    try:
        while True:
            await asyncio.sleep(3600)
    except KeyboardInterrupt:
        logger.info("Остановка...")
    finally:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()


if __name__ == '__main__':
    import asyncio
    asyncio.run(main())