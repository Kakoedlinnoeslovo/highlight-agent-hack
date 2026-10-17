"""Environment configuration. Credentials never enter result manifests."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    mode: str = "live"
    data_dir: Path = Path("data")
    output_dir: Path = Path("outputs")
    vast_base_url: str = ""
    vast_host_header: str = ""
    vast_username: str = ""
    vast_password: str = ""
    vast_access_token: str = ""
    vast_upload_bucket: str = ""
    vast_caption_model: str = ""
    wandb_api_key: str = ""
    wandb_project: str = ""
    wandb_model: str = ""
    wandb_base_url: str = "https://api.inference.wandb.ai/v1"
    request_timeout: float = 60

    @classmethod
    def from_env(cls) -> Settings:
        load_dotenv()
        mode = os.getenv("HIGHLIGHT_MODE", "live")
        if mode not in {"live", "offline_sample"}:
            raise ValueError("HIGHLIGHT_MODE must be live or offline_sample.")
        return cls(
            mode=mode,
            data_dir=Path(os.getenv("HIGHLIGHT_DATA_DIR", "data")).expanduser().resolve(),
            output_dir=Path(os.getenv("HIGHLIGHT_OUTPUT_DIR", "outputs")).expanduser().resolve(),
            vast_base_url=os.getenv("VAST_BASE_URL", "").rstrip("/"),
            vast_host_header=os.getenv("VAST_HOST_HEADER", "").strip(),
            vast_username=os.getenv("VAST_USERNAME", ""),
            vast_password=os.getenv("VAST_PASSWORD", ""),
            vast_access_token=os.getenv("VAST_ACCESS_TOKEN", ""),
            vast_upload_bucket=os.getenv("VAST_UPLOAD_BUCKET", ""),
            vast_caption_model=os.getenv("VAST_CAPTION_MODEL", ""),
            wandb_api_key=os.getenv("WANDB_API_KEY", ""),
            wandb_project=os.getenv("WANDB_PROJECT", ""),
            wandb_model=os.getenv("WANDB_MODEL", ""),
            wandb_base_url=os.getenv("WANDB_INFERENCE_BASE_URL", cls.wandb_base_url),
            request_timeout=float(os.getenv("HIGHLIGHT_REQUEST_TIMEOUT", "60")),
        )

    def missing(self) -> list[str]:
        if self.mode == "offline_sample":
            return []
        values = {
            "VAST_BASE_URL": self.vast_base_url,
            "VAST_ACCESS_TOKEN or VAST_USERNAME + VAST_PASSWORD": self.vast_access_token
            or (self.vast_username and self.vast_password),
            "WANDB_API_KEY": self.wandb_api_key,
            "WANDB_PROJECT (entity/project)": self.wandb_project,
            "WANDB_MODEL": self.wandb_model,
        }
        return [key for key, value in values.items() if not value]
