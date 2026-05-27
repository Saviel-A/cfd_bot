from sqlalchemy import Text, Numeric, Integer, TIMESTAMP, JSON, func
from sqlalchemy.orm import Mapped, mapped_column
from bot.db.session import Base


class Signal(Base):
    __tablename__ = "signals"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(Text)
    direction: Mapped[str] = mapped_column(Text)
    timeframe: Mapped[str] = mapped_column(Text)
    entry_price: Mapped[float] = mapped_column(Numeric)
    stop_loss: Mapped[float] = mapped_column(Numeric)
    tp: Mapped[float] = mapped_column(Numeric)
    sl_distance: Mapped[float | None] = mapped_column(Numeric)
    atr: Mapped[float | None] = mapped_column(Numeric)
    confluence_score: Mapped[int | None] = mapped_column(Integer)
    confluence_total: Mapped[int | None] = mapped_column(Integer)
    indicator_votes: Mapped[dict | None] = mapped_column(JSON)
    news_sentiment: Mapped[int] = mapped_column(Integer, default=0)
    candle_time: Mapped[object | None] = mapped_column(TIMESTAMP(timezone=True))
    fired_at: Mapped[object] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())
    outcome: Mapped[str] = mapped_column(Text, default="OPEN")
    outcome_at: Mapped[object | None] = mapped_column(TIMESTAMP(timezone=True))
