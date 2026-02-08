"""
FSM (Finite State Machine) состояния для бота.
"""
from aiogram.fsm.state import State, StatesGroup


class UploadMode(StatesGroup):
    """Состояние режима загрузки статистики."""
    active = State()  # Пользователь в режиме загрузки
