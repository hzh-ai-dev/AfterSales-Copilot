import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")


@dataclass
class Settings:
    data_dir: Path
    model_mode: str = "demo"
    api_key: str = ""
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-flash"
    user_token: str = "demo-user-token"
    other_token: str = "demo-other-token"
    admin_token: str = "demo-admin-token"
    max_tools: int = 12
    max_questions: int = 3

    def __post_init__(self):
        tokens = [self.user_token, self.other_token, self.admin_token]
        if any(not token for token in tokens) or len(set(tokens)) != 3:
            raise ValueError("三个演示身份令牌必须非空且互不相同。")

    @classmethod
    def from_env(cls):
        directory = Path(os.getenv("DATA_DIR", "data"))
        return cls(
            data_dir=directory if directory.is_absolute() else ROOT / directory,
            model_mode=os.getenv("MODEL_MODE", "demo"),
            api_key=os.getenv("DEEPSEEK_API_KEY", ""),
            base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
            model=os.getenv("DEEPSEEK_MODEL", "deepseek-flash"),
            user_token=os.getenv("DEMO_USER_TOKEN", "demo-user-token"),
            other_token=os.getenv("DEMO_OTHER_TOKEN", "demo-other-token"),
            admin_token=os.getenv("DEMO_ADMIN_TOKEN", "demo-admin-token"),
        )
