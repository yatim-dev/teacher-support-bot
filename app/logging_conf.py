# app/logging_conf.py
import logging
from logging.handlers import RotatingFileHandler


def setup_logging():
    fmt = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"

    root = logging.getLogger()
    root.setLevel(logging.INFO)

    # консоль
    sh = logging.StreamHandler()
    sh.setLevel(logging.INFO)
    sh.setFormatter(logging.Formatter(fmt))
    root.addHandler(sh)

    # файл (с ротацией)
    fh = RotatingFileHandler("bot.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    fh.setLevel(logging.INFO)
    fh.setFormatter(logging.Formatter(fmt))
    root.addHandler(fh)

    # чуть более разговорчиво для APScheduler при отладке
    logging.getLogger("apscheduler").setLevel(logging.INFO)
    logging.getLogger("aiogram").setLevel(logging.INFO)
