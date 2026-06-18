import os
import asyncio
from datetime import datetime
from dotenv import load_dotenv
from O365 import Account
from telegram import Bot

# Загружаем настройки из .env
load_dotenv()

CLIENT_ID = os.getenv('AZURE_CLIENT_ID')
CLIENT_SECRET = os.getenv('AZURE_CLIENT_SECRET')
TENANT_ID = os.getenv('AZURE_TENANT_ID')
TG_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
CHAT_ID = os.getenv('MY_CHAT_ID')

# Настройка Outlook
# Важно: scopes должны совпадать с теми, что вы добавили в Azure
credentials = (CLIENT_ID, CLIENT_SECRET)
account = Account(credentials, tenant_id=TENANT_ID)

async def send_tg_notification(text):
    """Отправка уведомления в Telegram"""
    if not TG_TOKEN or not CHAT_ID:
        print("⚠️ Не настроен Telegram Token или Chat ID")
        return
    bot = Bot(token=TG_TOKEN)
    await bot.send_message(chat_id=CHAT_ID, text=text)

def authenticate_outlook():
    """Первичная авторизация в Outlook"""
    if not account.is_authenticated:
        print("🔗 Нужно пройти авторизацию. Сейчас откроется ссылка...")
        # scopes=['mail.read', 'offline_access']
        account.authenticate(scopes=['https://graph.microsoft.com/Mail.Read', 'offline_access'])
        print("✅ Авторизация Outlook прошла успешно!")
    return True

async def check_mail():
    """Проверка почты на наличие новых писем"""
    mailbox = account.mailbox()
    # Получаем последние сообщения
    messages = mailbox.get_messages(limit=3, folder='Inbox')
    
    for message in messages:
        # Пример простого фильтра: пришло ли письмо в последние 10 минут
        # (Для продакшена лучше сохранять ID последнего обработанного письма)
        
        timestamp = message.created.strftime("%H:%M")
        alert_text = (
            f"📧 *Новое письмо в Outlook*\n\n"
            f"👤 *От:* {message.sender.address}\n"
            f"📝 *Тема:* {message.subject}\n"
            f"⏰ *Время:* {timestamp}"
        )
        
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Уведомление отправлено: {message.subject}")
        await send_tg_notification(alert_text)
        # Для теста обрабатываем только одно сообщение
        break

async def main():
    if not CLIENT_SECRET or "ВАШ_" in CLIENT_SECRET:
        print("❌ ОШИБКА: Вы не указали AZURE_CLIENT_SECRET в файле .env")
        return

    print("🚀 Бот запускается...")
    if authenticate_outlook():
        print("✅ Мониторинг запущен. Проверка каждые 5 минут.")
        while True:
            try:
                await check_mail()
            except Exception as e:
                print(f"❌ Ошибка при проверке: {e}")
            
            await asyncio.sleep(300) # 5 минут

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🛑 Бот остановлен.")
