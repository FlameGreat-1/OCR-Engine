import os
from typing import List, Optional
from pydantic import BaseSettings, Field

class Settings(BaseSettings):
    # API Configuration
    API_V1_STR: str = "/api/v1"
    PROJECT_NAME: str = "Invoice Processing System"
    X_API_KEY: str = Field(..., env="X_API_KEY")

    # File Upload Configuration
    MAX_UPLOAD_SIZE: int = 100 * 1024 * 1024  # 100MB
    ALLOWED_EXTENSIONS: set = {"pdf", "jpg", "jpeg", "png", "zip"}
    TEMP_FILE_DIR: str = Field(default="/tmp", env="TEMP_FILE_DIR")

    # CORS Configuration
    ALLOWED_ORIGINS: List[str] = Field(default=["*"], env="ALLOWED_ORIGINS")
    ALLOWED_HOSTS: List[str] = Field(default=["*"], env="ALLOWED_HOSTS")

    # Processing Configuration
    MULTI_PAGE_THRESHOLD: float = 0.95  # 95% confidence for multi-page detection
    INVOICE_NUMBER_ACCURACY: float = 0.95  # 95% accuracy for invoice number extraction
    TOTAL_MATH_ACCURACY: float = 1.0  # 100% accuracy for total calculations
    MAX_WORKERS: int = 5  # or any other appropriate number

    # Output Configuration
    OUTPUT_FORMATS: List[str] = Field(default=["csv", "excel"])

    # Google Cloud Vision Configuration
    GCV_CREDENTIALS: str = Field(..., env="GOOGLE_APPLICATION_CREDENTIALS")
    DOCAI_PROCESSOR_NAME: str = Field(..., env="DOCAI_PROCESSOR_NAME")

    # invoice2data Configuration
    INVOICE2DATA_TEMPLATES_DIR: str = Field(default="/app/invoice_templates", env="INVOICE2DATA_TEMPLATES_DIR")

    # Render-specific Configuration
    PORT: int = Field(default=10000, env="PORT")
    RENDER_URL: str = Field(..., env="RENDER_URL")

    # Database Configuration (for potential future use)
    DATABASE_URL: Optional[str] = Field(default=None, env="DATABASE_URL")

    # Celery Configuration
    CELERY_BROKER_URL: str = Field(..., env="CELERY_BROKER_URL")
    CELERY_RESULT_BACKEND: str = Field(..., env="CELERY_RESULT_BACKEND")

    # Logging Configuration
    LOG_LEVEL: str = Field(default="INFO", env="LOG_LEVEL")

    class Config:
        env_file = ".env"
        case_sensitive = True

settings = Settings()

def get_settings() -> Settings:
    return settings
