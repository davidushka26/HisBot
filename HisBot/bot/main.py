from __future__ import annotations

import asyncio
import logging
import secrets
import time
from dataclasses import dataclass

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import CallbackQuery, Message

from bot.config import Settings
from bot.data import (
    QUESTION_BANK,
    Question,
    get_front_event,
    get_front_events,
    get_front_title,
    get_question,
    random_question_ids,
)
from bot.keyboards import (
    duel_invite_keyboard,
    duel_question_keyboard,
    event_card_menu,
    events_menu,
    front_years_menu,
    fronts_menu,
    main_menu,
    question_keyboard,
)
from bot.storage import Duel, Storage


DUEL_QUESTIONS_COUNT = 10
QUIZ_QUESTIONS_COUNT = 10
INVITE_TTL_SECONDS = 60 * 60

router = Router()
storage: Storage
duel_locks: dict[int, asyncio.Lock] = {}


@dataclass
class QuizSession:
    question_ids: list[int]
    current_index: int = 0
    score: int = 0


quiz_sessions: dict[int, QuizSession] = {}


def _format_event_card(front_id: str, year: int, event_index: int) -> str:
    event = get_front_event(front_id, year, event_index)
    front_title = get_front_title(front_id)
    return (
        f"<b>{front_title}</b>\n"
        f"<b>{year}</b>\n\n"
        f"<b>{event.title}</b>\n\n"
        f"{event.description}"
    )


def _format_question_header(current: int, total: int, question: Question) -> str:
    return f"<b>Вопрос {current}/{total}</b>\n\n{question.question}"


def _format_duel_question(duel: Duel, player_name: str, question: Question) -> str:
    return (
        f"<b>Дуэль #{duel.id}</b>\n"
        f"Вопрос {duel.current_index + 1}/{len(duel.question_ids)}\n"
        f"Счет: {duel.score1} - {duel.score2}\n\n"
        f"{question.question}\n\n"
        f"Игрок: {player_name}"
    )


def _score_line(duel: Duel, player1_name: str, player2_name: str) -> str:
    return f"{player1_name}: {duel.score1}\n{player2_name}: {duel.score2}"


async def _remember_user(message_or_callback: Message | CallbackQuery) -> None:
    user = message_or_callback.from_user
    if user:
        await storage.upsert_user(user.id, user.first_name, user.username)


async def _send_home(target: Message | CallbackQuery) -> None:
    text = (
        "<b>HisBot</b>\n\n"
        "Здесь можно посмотреть главные события Второй мировой войны по фронтам и годам, "
        "пройти квиз или вызвать другого пользователя на дуэль."
    )
    if isinstance(target, CallbackQuery):
        if target.message:
            await target.message.edit_text(text, reply_markup=main_menu())
        await target.answer()
    else:
        await target.answer(text, reply_markup=main_menu())


async def _start_quiz(chat_id: int, bot: Bot, user_id: int) -> None:
    question_ids = random_question_ids(QUIZ_QUESTIONS_COUNT)
    quiz_sessions[user_id] = QuizSession(question_ids=question_ids)
    await _send_quiz_question(chat_id, bot, user_id)


async def _send_quiz_question(chat_id: int, bot: Bot, user_id: int) -> None:
    session = quiz_sessions.get(user_id)
    if session is None:
        await bot.send_message(chat_id, "Квиз не найден. Запусти новый через /quiz.")
        return

    if session.current_index >= len(session.question_ids):
        score = session.score
        total = len(session.question_ids)
        quiz_sessions.pop(user_id, None)
        await bot.send_message(
            chat_id,
            f"<b>Квиз завершен</b>\n\nТвой результат: {score}/{total}.",
            reply_markup=main_menu(),
        )
        return

    question = get_question(session.question_ids[session.current_index])
    await bot.send_message(
        chat_id,
        _format_question_header(session.current_index + 1, len(session.question_ids), question),
        reply_markup=question_keyboard(f"quiz:answer:{session.current_index}", question),
    )


async def _create_duel_invite(message: Message, bot: Bot, creator_id: int) -> None:
    code = secrets.token_urlsafe(8).replace("-", "_")
    await storage.create_pending_duel(code, creator_id)

    me = await bot.get_me()
    if not me.username:
        await message.answer("У бота нет username, поэтому ссылку для дуэли создать нельзя.")
        return

    link = f"https://t.me/{me.username}?start=duel_{code}"
    await message.answer(
        "<b>Приглашение в дуэль готово</b>\n\n"
        "Отправь эту ссылку другому участнику. Когда он откроет ее, начнется дуэль на 10 вопросов.\n\n"
        f"{link}",
        reply_markup=duel_invite_keyboard(link, code),
    )


async def _handle_duel_join(message: Message, code: str) -> None:
    pending = await storage.get_pending_duel(code)
    if pending is None:
        await message.answer("Это приглашение уже использовано, отменено или не найдено.")
        return

    if pending.creator_id == message.from_user.id:
        await message.answer("Это твоя ссылка. Отправь ее другому участнику, чтобы начать дуэль.")
        return

    if pending.created_at + INVITE_TTL_SECONDS < int(time.time()):
        await storage.delete_pending_duel(code)
        await message.answer("Приглашение устарело. Пусть первый игрок создаст новое через /duel.")
        return

    player1_id = pending.creator_id
    player2_id = message.from_user.id
    exclude = await storage.get_recent_question_ids_for_players(player1_id, player2_id)
    question_ids = random_question_ids(DUEL_QUESTIONS_COUNT, exclude=exclude)
    duel = await storage.create_duel(code, player1_id, player2_id, question_ids)
    await storage.delete_pending_duel(code)

    player1_name = await storage.get_user_name(player1_id)
    player2_name = await storage.get_user_name(player2_id)
    intro = (
        f"<b>Дуэль началась</b>\n\n"
        f"{player1_name} против {player2_name}\n"
        "Всего 10 вопросов. Первый правильный ответ на каждом вопросе дает 1 балл."
    )
    await message.bot.send_message(player1_id, intro)
    await message.bot.send_message(player2_id, intro)
    await _send_duel_question(message.bot, duel.id)


async def _send_duel_question(bot: Bot, duel_id: int) -> None:
    duel = await storage.get_duel(duel_id)
    if duel is None or duel.status != "active":
        return

    question = get_question(duel.question_ids[duel.current_index])
    for player_id in (duel.player1_id, duel.player2_id):
        player_name = await storage.get_user_name(player_id)
        sent = await bot.send_message(
            player_id,
            _format_duel_question(duel, player_name, question),
            reply_markup=duel_question_keyboard(duel.id, duel.current_index, question),
        )
        await storage.record_duel_message(duel.id, duel.current_index, player_id, sent.message_id)


async def _finish_duel(bot: Bot, duel: Duel) -> None:
    player1_name = await storage.get_user_name(duel.player1_id)
    player2_name = await storage.get_user_name(duel.player2_id)

    if duel.score1 > duel.score2:
        result = f"Победил {player1_name}."
    elif duel.score2 > duel.score1:
        result = f"Победил {player2_name}."
    else:
        result = "Ничья."

    text = (
        f"<b>Дуэль #{duel.id} завершена</b>\n\n"
        f"{_score_line(duel, player1_name, player2_name)}\n\n"
        f"{result}"
    )
    await bot.send_message(duel.player1_id, text, reply_markup=main_menu())
    await bot.send_message(duel.player2_id, text, reply_markup=main_menu())


async def _edit_duel_question_as_answered(bot: Bot, duel: Duel, winner_id: int) -> None:
    question = get_question(duel.question_ids[duel.current_index])
    winner_name = await storage.get_user_name(winner_id)
    player1_name = await storage.get_user_name(duel.player1_id)
    player2_name = await storage.get_user_name(duel.player2_id)
    messages = await storage.get_duel_messages(duel.id, duel.current_index)
    text = (
        f"<b>Вопрос {duel.current_index + 1}/{len(duel.question_ids)}</b>\n\n"
        f"{question.question}\n\n"
        f"Правильный ответ: <b>{question.options[question.answer]}</b>\n"
        f"Первым ответил: <b>{winner_name}</b>\n\n"
        f"{question.explanation}\n\n"
        f"Счет:\n{_score_line(duel, player1_name, player2_name)}"
    )
    for user_id, message_id in messages.items():
        try:
            await bot.edit_message_text(text, chat_id=user_id, message_id=message_id)
        except Exception as error:
            logging.warning("Cannot edit duel message %s for %s: %s", message_id, user_id, error)


async def _edit_duel_question_as_waiting(callback: CallbackQuery, duel: Duel, selected: int) -> None:
    if not callback.message:
        return

    question = get_question(duel.question_ids[duel.current_index])
    player1_name = await storage.get_user_name(duel.player1_id)
    player2_name = await storage.get_user_name(duel.player2_id)
    text = (
        f"<b>Вопрос {duel.current_index + 1}/{len(duel.question_ids)}</b>\n\n"
        f"{question.question}\n\n"
        f"Твой ответ: <b>{question.options[selected]}</b>\n"
        "Неверно. Теперь ждем ответ соперника.\n\n"
        f"Счет:\n{_score_line(duel, player1_name, player2_name)}"
    )
    await callback.message.edit_text(text)


async def _edit_duel_question_without_winner(bot: Bot, duel: Duel) -> None:
    question = get_question(duel.question_ids[duel.current_index])
    player1_name = await storage.get_user_name(duel.player1_id)
    player2_name = await storage.get_user_name(duel.player2_id)
    messages = await storage.get_duel_messages(duel.id, duel.current_index)
    text = (
        f"<b>Вопрос {duel.current_index + 1}/{len(duel.question_ids)}</b>\n\n"
        f"{question.question}\n\n"
        f"Правильный ответ: <b>{question.options[question.answer]}</b>\n"
        "Оба игрока ответили неверно. Балл не начислен.\n\n"
        f"{question.explanation}\n\n"
        f"Счет:\n{_score_line(duel, player1_name, player2_name)}"
    )
    for user_id, message_id in messages.items():
        try:
            await bot.edit_message_text(text, chat_id=user_id, message_id=message_id)
        except Exception as error:
            logging.warning("Cannot edit duel message %s for %s: %s", message_id, user_id, error)


async def _advance_duel_after_pause(bot: Bot, duel_id: int) -> None:
    await asyncio.sleep(2)
    next_duel = await storage.advance_duel(duel_id)
    if next_duel is None:
        return
    if next_duel.status == "finished":
        await _finish_duel(bot, next_duel)
    else:
        await _send_duel_question(bot, next_duel.id)


@router.message(CommandStart())
async def start(message: Message, command: CommandObject) -> None:
    await _remember_user(message)
    if command.args and command.args.startswith("duel_"):
        await _handle_duel_join(message, command.args.removeprefix("duel_"))
        return
    await _send_home(message)


@router.message(Command("help"))
async def help_command(message: Message) -> None:
    await _remember_user(message)
    await message.answer(
        "<b>Команды</b>\n\n"
        "/info - события Второй мировой по фронтам и годам\n"
        "/quiz - одиночный квиз на 10 вопросов\n"
        "/duel - создать приглашение в дуэль\n"
        "/start - главное меню"
    )


@router.message(Command("info"))
async def info_command(message: Message) -> None:
    await _remember_user(message)
    await message.answer("Выбери фронт:", reply_markup=fronts_menu())


@router.message(Command("quiz"))
async def quiz_command(message: Message) -> None:
    await _remember_user(message)
    await _start_quiz(message.chat.id, message.bot, message.from_user.id)


@router.message(Command("duel"))
async def duel_command(message: Message, bot: Bot) -> None:
    await _remember_user(message)
    await _create_duel_invite(message, bot, message.from_user.id)


@router.callback_query(F.data == "menu:home")
async def menu_home(callback: CallbackQuery) -> None:
    await _remember_user(callback)
    await _send_home(callback)


@router.callback_query(F.data == "menu:timeline")
async def menu_timeline(callback: CallbackQuery) -> None:
    await _remember_user(callback)
    if callback.message:
        await callback.message.edit_text("Выбери фронт:", reply_markup=fronts_menu())
    await callback.answer()


@router.callback_query(F.data.startswith("info:front:"))
async def show_front_years(callback: CallbackQuery) -> None:
    await _remember_user(callback)
    front_id = callback.data.split(":")[2]
    if callback.message:
        await callback.message.edit_text(
            f"<b>{get_front_title(front_id)}</b>\n\nВыбери год:",
            reply_markup=front_years_menu(front_id),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("info:year:"))
async def show_front_events(callback: CallbackQuery) -> None:
    await _remember_user(callback)
    _, _, front_id, year_text = callback.data.split(":")
    year = int(year_text)
    events_count = len(get_front_events(front_id, year))
    if callback.message:
        await callback.message.edit_text(
            f"<b>{get_front_title(front_id)}</b>\n"
            f"<b>{year}</b>\n\n"
            f"Выбери событие ({events_count}):",
            reply_markup=events_menu(front_id, year),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("info:event:"))
async def show_front_event(callback: CallbackQuery) -> None:
    await _remember_user(callback)
    _, _, front_id, year_text, event_index_text = callback.data.split(":")
    year = int(year_text)
    event_index = int(event_index_text)
    if callback.message:
        await callback.message.edit_text(
            _format_event_card(front_id, year, event_index),
            reply_markup=event_card_menu(front_id, year),
        )
    await callback.answer()


@router.callback_query(F.data == "quiz:start")
async def quiz_button(callback: CallbackQuery) -> None:
    await _remember_user(callback)
    if callback.message:
        await callback.message.answer("Начинаем квиз на 10 случайных вопросов.")
        await _start_quiz(callback.message.chat.id, callback.bot, callback.from_user.id)
    await callback.answer()


@router.callback_query(F.data.startswith("quiz:answer:"))
async def quiz_answer(callback: CallbackQuery) -> None:
    await _remember_user(callback)
    user_id = callback.from_user.id
    session = quiz_sessions.get(user_id)
    if session is None:
        await callback.answer("Квиз уже завершен. Запусти новый через /quiz.", show_alert=True)
        return

    parts = callback.data.split(":")
    question_index = int(parts[2])
    selected = int(parts[3])
    if question_index != session.current_index:
        await callback.answer("Этот вопрос уже не активен.", show_alert=True)
        return

    question = get_question(session.question_ids[session.current_index])
    is_correct = selected == question.answer
    if is_correct:
        session.score += 1

    result = "Верно." if is_correct else "Неверно."
    text = (
        f"{result}\n\n"
        f"Правильный ответ: <b>{question.options[question.answer]}</b>\n"
        f"{question.explanation}\n\n"
        f"Счет: {session.score}/{session.current_index + 1}"
    )
    if callback.message:
        await callback.message.edit_text(text)
        session.current_index += 1
        await _send_quiz_question(callback.message.chat.id, callback.bot, user_id)
    await callback.answer()


@router.callback_query(F.data == "duel:new")
async def duel_button(callback: CallbackQuery, bot: Bot) -> None:
    await _remember_user(callback)
    if callback.message:
        await _create_duel_invite(callback.message, bot, callback.from_user.id)
    await callback.answer()


@router.callback_query(F.data.startswith("duel:cancel:"))
async def duel_cancel(callback: CallbackQuery) -> None:
    await _remember_user(callback)
    code = callback.data.rsplit(":", 1)[1]
    cancelled = await storage.cancel_pending_duel(code, callback.from_user.id)
    if callback.message:
        if cancelled:
            await callback.message.edit_text("Приглашение в дуэль отменено.", reply_markup=main_menu())
        else:
            await callback.message.answer("Не получилось отменить приглашение: оно уже использовано или принадлежит другому игроку.")
    await callback.answer()


@router.callback_query(F.data.startswith("duel:answer:"))
async def duel_answer(callback: CallbackQuery) -> None:
    await _remember_user(callback)
    parts = callback.data.split(":")
    duel_id = int(parts[2])
    question_index = int(parts[3])
    selected = int(parts[4])

    lock = duel_locks.setdefault(duel_id, asyncio.Lock())
    async with lock:
        duel = await storage.get_duel(duel_id)
        if duel is None or duel.status != "active":
            await callback.answer("Эта дуэль уже завершена.", show_alert=True)
            return

        if callback.from_user.id not in (duel.player1_id, duel.player2_id):
            await callback.answer("Ты не участник этой дуэли.", show_alert=True)
            return

        if question_index != duel.current_index:
            await callback.answer("Этот вопрос уже не активен.", show_alert=True)
            return

        if duel.current_winner_id is not None:
            await callback.answer("На этот вопрос уже ответили первым.", show_alert=True)
            return

        answers = await storage.get_duel_answers(duel.id, duel.current_index)
        if callback.from_user.id in answers:
            await callback.answer("Ты уже ответил на этот вопрос. Ждем соперника.", show_alert=True)
            return

        question = get_question(duel.question_ids[duel.current_index])
        is_correct = selected == question.answer
        recorded = await storage.record_duel_answer(
            duel.id,
            duel.current_index,
            callback.from_user.id,
            selected,
            is_correct,
        )
        if not recorded:
            await callback.answer("Ты уже ответил на этот вопрос. Ждем соперника.", show_alert=True)
            return

        if not is_correct:
            await callback.answer("Неверно. Теперь ждем ответ соперника.")
            await _edit_duel_question_as_waiting(callback, duel, selected)

            answers = await storage.get_duel_answers(duel.id, duel.current_index)
            both_players_answered = duel.player1_id in answers and duel.player2_id in answers
            if both_players_answered and not any(answers.values()):
                await _edit_duel_question_without_winner(callback.bot, duel)
                await _advance_duel_after_pause(callback.bot, duel.id)
            return

        updated_duel = await storage.mark_question_winner(duel.id, callback.from_user.id)
        if updated_duel is None:
            await callback.answer("Не удалось засчитать ответ.", show_alert=True)
            return

        await callback.answer("Верно. Ты забрал балл.")
        await _edit_duel_question_as_answered(callback.bot, updated_duel, callback.from_user.id)
        await _advance_duel_after_pause(callback.bot, duel.id)


@router.message()
async def fallback(message: Message) -> None:
    await _remember_user(message)
    await message.answer(
        "Я понимаю команды /info, /quiz и /duel. Можно также открыть главное меню через /start.",
        reply_markup=main_menu(),
    )


async def main() -> None:
    global storage

    logging.basicConfig(level=logging.INFO)
    settings = Settings.load()
    storage = Storage(settings.db_path)
    await storage.init()

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dispatcher = Dispatcher()
    dispatcher.include_router(router)

    try:
        logging.info("Question bank size: %s", len(QUESTION_BANK))
        await dispatcher.start_polling(bot)
    finally:
        await bot.session.close()
        await storage.close()


if __name__ == "__main__":
    asyncio.run(main())
