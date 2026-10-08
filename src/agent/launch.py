"""Совместимый AI-вход в единый сервер приложения."""
import sys
from src.core.launch import main

if __name__ == "__main__":
    main(["--with-ai", *sys.argv[1:]])
