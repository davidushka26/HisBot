from __future__ import annotations

from urllib.parse import quote

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.data import Question, get_front_events, get_front_title, get_front_years, get_fronts


def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="События по фронтам", callback_data="menu:timeline")],
            [InlineKeyboardButton(text="Одиночный квиз", callback_data="quiz:start")],
            [InlineKeyboardButton(text="Создать дуэль", callback_data="duel:new")],
        ]
    )


def fronts_menu() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=front.title, callback_data=f"info:front:{front.id}")]
        for front in get_fronts()
    ]
    rows.append([InlineKeyboardButton(text="Назад", callback_data="menu:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def front_years_menu(front_id: str) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=str(year), callback_data=f"info:year:{front_id}:{year}")]
        for year in get_front_years(front_id)
    ]
    rows.append([InlineKeyboardButton(text="Назад к фронтам", callback_data="menu:timeline")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def events_menu(front_id: str, year: int) -> InlineKeyboardMarkup:
    rows = []
    for index, event in enumerate(get_front_events(front_id, year)):
        title = event.title if len(event.title) <= 58 else f"{event.title[:55]}..."
        rows.append(
            [InlineKeyboardButton(text=title, callback_data=f"info:event:{front_id}:{year}:{index}")]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text=f"Назад к {get_front_title(front_id)}",
                callback_data=f"info:front:{front_id}",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def event_card_menu(front_id: str, year: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Назад к событиям", callback_data=f"info:year:{front_id}:{year}")],
            [InlineKeyboardButton(text="К фронтам", callback_data="menu:timeline")],
        ]
    )


def question_keyboard(prefix: str, question: Question) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=option, callback_data=f"{prefix}:{index}")]
        for index, option in enumerate(question.options)
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def duel_question_keyboard(duel_id: int, question_index: int, question: Question) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=option,
                callback_data=f"duel:answer:{duel_id}:{question_index}:{index}",
            )
        ]
        for index, option in enumerate(question.options)
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def duel_invite_keyboard(link: str, code: str) -> InlineKeyboardMarkup:
    share_url = (
        "https://t.me/share/url?"
        f"url={quote(link, safe='')}&"
        f"text={quote('Присоединяйся к дуэли по истории в HisBot', safe='')}"
    )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Поделиться приглашением", url=share_url)],
            [InlineKeyboardButton(text="Отменить дуэль", callback_data=f"duel:cancel:{code}")],
        ]
    )
