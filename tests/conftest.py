import os
import secrets
import sys
from pathlib import Path

os.environ.setdefault('JWT_SECRET',secrets.token_urlsafe(48))
os.environ.setdefault('CONFIG_PATH',str(Path(__file__).resolve().parent/'fixtures/config.yaml'))
os.environ.setdefault('PYTHONDONTWRITEBYTECODE','1')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
