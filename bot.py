import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
from database import init_db, get_session
from models import User, Ticket, Message
from config import TELEGRAM_TOKEN, ADMIN_IDS

logging.basicConfig(level=logging.INFO)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /start"""
    user = update.effective_user
    
    async with get_session() as session:
        db_user = await session.get(User, user.id)
        if not db_user:
            db_user = User(
                telegram_id=user.id,
                username=user.username,
                first_name=user.first_name,
                is_admin=user.id in ADMIN_IDS
            )
            session.add(db_user)
            await session.commit()
    
    keyboard = [
        [InlineKeyboardButton("Создать тикет", callback_data="create_ticket")],
        [InlineKeyboardButton("Мои тикеты", callback_data="my_tickets")]
    ]
    
    if user.id in ADMIN_IDS:
        keyboard.append([InlineKeyboardButton("Админ панель", callback_data="admin_panel")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"Добро пожаловать в техподдержку, {user.first_name}!",
        reply_markup=reply_markup
    )

async def create_ticket_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка нажатия на 'Создать тикет'"""
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_text("Введите заголовок тикета:")
    context.user_data['creating_ticket'] = 'title'

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка текстовых сообщений"""
    user_id = update.effective_user.id
    
    # Обработка ответа администратора на тикет
    if 'replying_to_ticket' in context.user_data:
        if user_id not in ADMIN_IDS:
            return
        
        ticket_id = context.user_data['replying_to_ticket']
        reply_text = update.message.text
        
        async with get_session() as session:
            from sqlalchemy import select
            ticket_result = await session.execute(select(Ticket).where(Ticket.id == ticket_id))
            ticket = ticket_result.scalar_one_or_none()
            
            if ticket:
                # Сохраняем ответ администратора
                message = Message(
                    ticket_id=ticket_id,
                    sender_id=user_id,
                    content=reply_text,
                    is_admin=True
                )
                session.add(message)
                await session.commit()
                
                # Уведомляем пользователя о ответе
                try:
                    await context.bot.send_message(
                        ticket.user_id,
                        f"💬 Новый ответ на тикет #{ticket_id}:\n\n{reply_text}"
                    )
                except:
                    pass
                
                await update.message.reply_text(f"✅ Ответ отправлен на тикет #{ticket_id}")
                
                # Показываем обновленный тикет
                context.user_data['ticket_callback_data'] = f"ticket_{ticket_id}"
                await ticket_callback(update, context)
                
                del context.user_data['replying_to_ticket']
                return
    
    # Обработка создания тикета
    if 'creating_ticket' in context.user_data:
        stage = context.user_data['creating_ticket']
        
        if stage == 'title':
            context.user_data['ticket_title'] = update.message.text
            await update.message.reply_text("Введите описание проблемы:")
            context.user_data['creating_ticket'] = 'description'
            
        elif stage == 'description':
            title = context.user_data['ticket_title']
            description = update.message.text
            
            async with get_session() as session:
                ticket = Ticket(
                    user_id=user_id,
                    title=title,
                    description=description
                )
                session.add(ticket)
                await session.commit()
                await session.refresh(ticket)
                
                message = Message(
                    ticket_id=ticket.id,
                    sender_id=user_id,
                    content=description,
                    is_admin=False
                )
                session.add(message)
                await session.commit()
            
            for admin_id in ADMIN_IDS:
                try:
                    await context.bot.send_message(
                        admin_id,
                        f"🆕 Новый тикет #{ticket.id}\n"
                        f"От: {update.effective_user.first_name}\n"
                        f"Заголовок: {title}\n"
                        f"Описание: {description}"
                    )
                except:
                    pass
            
            await update.message.reply_text(f"✅ Тикет #{ticket.id} создан! Мы скоро ответим.")
            
            del context.user_data['creating_ticket']
            del context.user_data['ticket_title']

async def my_tickets_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать тикеты пользователя"""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    
    async with get_session() as session:
        from sqlalchemy import select
        result = await session.execute(
            select(Ticket).where(Ticket.user_id == user_id).order_by(Ticket.created_at.desc())
        )
        tickets = result.scalars().all()
    
    if not tickets:
        await query.edit_message_text("У вас пока нет тикетов.")
        return
    
    text = "📋 Ваши тикеты:\n\n"
    for ticket in tickets:
        status_emoji = {"open": "🔵", "in_progress": "🟡", "closed": "🟢"}
        text += f"#{ticket.id} {status_emoji.get(ticket.status, '⚪')} {ticket.title}\n"
        text += f"Статус: {ticket.status}\n"
        text += f"Создан: {ticket.created_at.strftime('%Y-%m-%d %H:%M')}\n\n"
    
    await query.edit_message_text(text)

async def admin_panel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Админ панель"""
    query = update.callback_query
    await query.answer()
    
    if update.effective_user.id not in ADMIN_IDS:
        await query.edit_message_text("У вас нет доступа к админ панели.")
        return
    
    keyboard = [
        [InlineKeyboardButton("Все тикеты", callback_data="all_tickets")],
        [InlineKeyboardButton("Открытые тикеты", callback_data="open_tickets")],
        [InlineKeyboardButton("Назад", callback_data="back_to_main")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text("🛠️ Админ панель:", reply_markup=reply_markup)

async def all_tickets_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать все тикеты"""
    query = update.callback_query
    await query.answer()
    
    if update.effective_user.id not in ADMIN_IDS:
        return
    
    async with get_session() as session:
        from sqlalchemy import select
        result = await session.execute(
            select(Ticket).order_by(Ticket.created_at.desc())
        )
        tickets = result.scalars().all()
    
    if not tickets:
        await query.edit_message_text("Тикетов пока нет.")
        return
    
    text = "📋 Все тикеты:\n\n"
    keyboard = []
    
    for ticket in tickets:
        status_emoji = {"open": "🔵", "in_progress": "🟡", "closed": "🟢"}
        text += f"#{ticket.id} {status_emoji.get(ticket.status, '⚪')} {ticket.title}\n"
        text += f"Статус: {ticket.status}\n\n"
        
        keyboard.append([InlineKeyboardButton(f"Тикет #{ticket.id}", callback_data=f"ticket_{ticket.id}")])
    
    keyboard.append([InlineKeyboardButton("Назад", callback_data="admin_panel")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(text, reply_markup=reply_markup)

async def ticket_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Просмотр тикета"""
    query = update.callback_query
    await query.answer()
    
    ticket_id = int(query.data.split("_")[1])
    
    async with get_session() as session:
        from sqlalchemy import select
        ticket_result = await session.execute(select(Ticket).where(Ticket.id == ticket_id))
        ticket = ticket_result.scalar_one_or_none()
        
        if not ticket:
            await query.edit_message_text("Тикет не найден.")
            return
        
        messages_result = await session.execute(
            select(Message).where(Message.ticket_id == ticket_id).order_by(Message.created_at)
        )
        messages = messages_result.scalars().all()
        
        user_result = await session.execute(select(User).where(User.id == ticket.user_id))
        user = user_result.scalar_one_or_none()
    
    text = f"🎫 Тикет #{ticket.id}\n"
    text += f"👤 От: {user.first_name} (@{user.username})\n"
    text += f"📝 Заголовок: {ticket.title}\n"
    text += f"📊 Статус: {ticket.status}\n"
    text += f"🕐 Создан: {ticket.created_at.strftime('%Y-%m-%d %H:%M')}\n\n"
    text += "💬 Сообщения:\n\n"
    
    for msg in messages:
        sender = "👨‍💼 Админ" if msg.is_admin else f"👤 {user.first_name}"
        text += f"{sender}: {msg.content}\n"
        text += f"🕐 {msg.created_at.strftime('%Y-%m-%d %H:%M')}\n\n"
    
    keyboard = []
    
    if update.effective_user.id in ADMIN_IDS:
        if ticket.status == "open":
            keyboard.append([InlineKeyboardButton("В работе", callback_data=f"status_in_progress_{ticket_id}")])
        elif ticket.status == "in_progress":
            keyboard.append([InlineKeyboardButton("Закрыть", callback_data=f"status_closed_{ticket_id}")])
        
        keyboard.append([InlineKeyboardButton("Ответить", callback_data=f"reply_{ticket_id}")])
    
    keyboard.append([InlineKeyboardButton("Назад", callback_data="admin_panel")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(text, reply_markup=reply_markup)

async def reply_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начать ответ на тикет"""
    query = update.callback_query
    await query.answer()
    
    if update.effective_user.id not in ADMIN_IDS:
        return
    
    ticket_id = int(query.data.split("_")[1])
    context.user_data['replying_to_ticket'] = ticket_id
    
    await query.edit_message_text("Введите ваш ответ на тикет:")

async def status_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Изменить статус тикета"""
    query = update.callback_query
    await query.answer()
    
    if update.effective_user.id not in ADMIN_IDS:
        return
    
    parts = query.data.split("_")
    status = parts[1] + "_" + parts[2]
    ticket_id = int(parts[3])
    
    async with get_session() as session:
        from sqlalchemy import select
        ticket_result = await session.execute(select(Ticket).where(Ticket.id == ticket_id))
        ticket = ticket_result.scalar_one_or_none()
        
        if ticket:
            ticket.status = status
            await session.commit()
            
            # Уведомляем пользователя об изменении статуса
            try:
                status_text = {"in_progress": "в работе", "closed": "закрыт"}
                await context.bot.send_message(
                    ticket.user_id,
                    f"📊 Статус вашего тикета #{ticket.id} изменен на: {status_text.get(status, status)}"
                )
            except:
                pass
    
    await ticket_callback(update, context)

async def main():
    """Запуск бота"""
    await init_db()
    
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(create_ticket_callback, pattern="^create_ticket$"))
    application.add_handler(CallbackQueryHandler(my_tickets_callback, pattern="^my_tickets$"))
    application.add_handler(CallbackQueryHandler(admin_panel_callback, pattern="^admin_panel$"))
    application.add_handler(CallbackQueryHandler(all_tickets_callback, pattern="^all_tickets$"))
    application.add_handler(CallbackQueryHandler(ticket_callback, pattern="^ticket_"))
    application.add_handler(CallbackQueryHandler(reply_callback, pattern="^reply_"))
    application.add_handler(CallbackQueryHandler(status_callback, pattern="^status_"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    print("Бот запущен...")
    application.run_polling()

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
