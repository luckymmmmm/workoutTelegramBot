import os
import asyncio
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, selectinload
from sqlalchemy import select, delete, ForeignKey

load_dotenv("token.env")
BOT_TOKEN = os.getenv("BOT_TOKEN")

engine = create_async_engine("sqlite+aiosqlite:///workouts.db", echo=False)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)

class Base(DeclarativeBase):
    pass

class WorkoutDay(Base):
    __tablename__ = "workout_days"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int]
    name: Mapped[str]
    exercises = relationship("Exercise", back_populates="day", cascade="all, delete-orphan")

class Exercise(Base):
    __tablename__ = "exercises"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    day_id: Mapped[int] = mapped_column(ForeignKey("workout_days.id", ondelete="CASCADE"))
    name: Mapped[str]
    weight: Mapped[float] = mapped_column(nullable=True)
    reps: Mapped[int] = mapped_column(nullable=True)
    
    day = relationship("WorkoutDay", back_populates="exercises")

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

class WorkoutState(StatesGroup):
    waiting_for_day_name = State()
    waiting_for_ex_name = State()
    waiting_for_result = State()

@dp.message(F.text == "/start")
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await show_main_menu(message.from_user.id, message)

async def show_main_menu(user_id: int, target):
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(WorkoutDay).where(WorkoutDay.user_id == user_id))
        days = result.scalars().all()

    buttons = [[InlineKeyboardButton(text=day.name, callback_data=f"day_{day.id}")] for day in days]
    buttons.append([InlineKeyboardButton(text="➕ Додати тренування", callback_data="add_day")])
    
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    text = "Головне меню. Обери тренування або створи нове:"
    
    if isinstance(target, Message):
        await target.answer(text, reply_markup=kb)
    else:
        await target.message.edit_text(text, reply_markup=kb)

@dp.callback_query(F.data == "add_day")
async def ask_day_name(callback: CallbackQuery, state: FSMContext):
    await state.set_state(WorkoutState.waiting_for_day_name)
    await callback.message.edit_text("Введи назву тренування (наприклад: Спина біцепс):")

@dp.message(WorkoutState.waiting_for_day_name)
async def save_day_name(message: Message, state: FSMContext):
    async with AsyncSessionLocal() as session:
        new_day = WorkoutDay(user_id=message.from_user.id, name=message.text)
        session.add(new_day)
        await session.commit()
    await state.clear()
    await show_main_menu(message.from_user.id, message)

async def render_day_menu(day_id: int, target):
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(WorkoutDay).options(selectinload(WorkoutDay.exercises)).where(WorkoutDay.id == day_id)
        )
        day = result.scalar_one_or_none()

    if not day:
        return

    text = f"🏋️‍♂️ Тренування: **{day.name}**\n\n"
    buttons = []
    
    for ex in day.exercises:
        if ex.weight is not None and ex.reps is not None:
            res_text = f"{ex.weight}кг x {ex.reps}"
        else:
            res_text = "немає даних"
            
        text += f"🔹 {ex.name}: {res_text}\n"
        buttons.append([InlineKeyboardButton(text=f"{ex.name}", callback_data=f"ex_{ex.id}")])

    buttons.append([InlineKeyboardButton(text="➕ Додати нову вправу", callback_data=f"add_ex_{day.id}")])
    buttons.append([InlineKeyboardButton(text="🔙 Назад до меню", callback_data="back_main")])
    buttons.append([InlineKeyboardButton(text="🗑 Видалити день", callback_data=f"del_day_{day.id}")])
    
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    
    if isinstance(target, CallbackQuery):
        await target.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")
    elif isinstance(target, Message):
        await target.answer(text, reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data.startswith("day_"))
async def show_day_menu(callback: CallbackQuery):
    day_id = int(callback.data.split("_")[1])
    await render_day_menu(day_id, callback)

@dp.callback_query(F.data == "back_main")
async def back_main(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await show_main_menu(callback.from_user.id, callback)

@dp.callback_query(F.data.startswith("add_ex_"))
async def ask_ex_name(callback: CallbackQuery, state: FSMContext):
    day_id = int(callback.data.split("_")[2])
    await state.update_data(current_day_id=day_id)
    await state.set_state(WorkoutState.waiting_for_ex_name)
    await callback.message.edit_text("Введи назву вправи:")

@dp.message(WorkoutState.waiting_for_ex_name)
async def save_ex_name(message: Message, state: FSMContext):
    data = await state.get_data()
    day_id = data['current_day_id']
    
    async with AsyncSessionLocal() as session:
        new_ex = Exercise(day_id=day_id, name=message.text)
        session.add(new_ex)
        await session.commit()
        
    await state.clear()
    await render_day_menu(day_id, message)

async def render_exercise_menu(ex_id: int, target):
    async with AsyncSessionLocal() as session:
        ex = await session.get(Exercise, ex_id)

    if not ex:
        return

    text = f"Вправа: **{ex.name}**\n\n"
    if ex.weight is not None and ex.reps is not None:
        text += f"💪 Робоча вага: **{ex.weight} кг**\n🔄 Повторення: **{ex.reps}**\n"
    else:
        text += "Результатів ще немає.\n"
        
    buttons = [
        [InlineKeyboardButton(text="🔄 Оновити результат (Вага Повтори)", callback_data=f"update_res_{ex.id}")],
        [InlineKeyboardButton(text="🔙 Назад до тренування", callback_data=f"day_{ex.day_id}")],
        [InlineKeyboardButton(text="🗑 Видалити вправу", callback_data=f"del_ex_{ex.id}")]
    ]
    
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    if isinstance(target, CallbackQuery):
        await target.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")
    elif isinstance(target, Message):
        await target.answer(text, reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data.startswith("ex_"))
async def show_exercise_menu(callback: CallbackQuery):
    ex_id = int(callback.data.split("_")[1])
    await render_exercise_menu(ex_id, callback)

@dp.callback_query(F.data.startswith("update_res_"))
async def ask_result_data(callback: CallbackQuery, state: FSMContext):
    ex_id = int(callback.data.split("_")[2])
    await state.update_data(current_ex_id=ex_id)
    await state.set_state(WorkoutState.waiting_for_result)
    await callback.message.edit_text("Введи нову вагу та повторення (наприклад: `140 12`):", parse_mode="Markdown")

@dp.message(WorkoutState.waiting_for_result)
async def save_result(message: Message, state: FSMContext):
    data = await state.get_data()
    ex_id = data['current_ex_id']

    try:
        w_str, r_str = message.text.split()
        weight, reps = float(w_str), int(r_str)
    except ValueError:
        await message.answer("❌ Формат невірний. Напиши 2 числа через пробіл (наприклад: 140 12).")
        return

    async with AsyncSessionLocal() as session:
        ex = await session.get(Exercise, ex_id)
        if ex:
            ex.weight = weight
            ex.reps = reps
            await session.commit()

    await state.clear()
    await render_exercise_menu(ex_id, message)

@dp.callback_query(F.data.startswith("del_day_"))
async def delete_day(callback: CallbackQuery):
    day_id = int(callback.data.split("_")[2])
    async with AsyncSessionLocal() as session:
        await session.execute(delete(WorkoutDay).where(WorkoutDay.id == day_id))
        await session.commit()
    await show_main_menu(callback.from_user.id, callback)

@dp.callback_query(F.data.startswith("del_ex_"))
async def delete_exercise(callback: CallbackQuery):
    ex_id = int(callback.data.split("_")[2])
    async with AsyncSessionLocal() as session:
        ex = await session.get(Exercise, ex_id)
        day_id = ex.day_id
        await session.execute(delete(Exercise).where(Exercise.id == ex_id))
        await session.commit()
        
    await render_day_menu(day_id, callback)

async def main():
    await init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())