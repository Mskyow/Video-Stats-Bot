"""
FSM states for the bot.
"""
from aiogram.fsm.state import State, StatesGroup


class UploadMode(StatesGroup):
    active = State()


class YouTubeUploadMode(StatesGroup):
    active = State()


class FunnelUploadMode(StatesGroup):
    active = State()
