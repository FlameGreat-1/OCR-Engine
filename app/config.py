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
    BATCH_SIZE: int = 5
    ALLOWED_EXTENSIONS: set = {"pdf", "jpg", "jpeg", "png", "zip"}
    TEMP_FILE_DIR: str = Field(default="/tmp", env="TEMP_FILE_DIR")

    # CORS Configuration
    ALLOWED_ORIGINS: List[str] = Field(default=["*"], env="ALLOWED_ORIGINS")
    ALLOWED_HOSTS: List[str] = Field(default=["*"], env="ALLOWED_HOSTS")

    # Processing Configuration
    MULTI_PAGE_THRESHOLD: float = 0.95  # 95% confidence for multi-page detection
    INVOICE_NUMBER_ACCURACY: float = 0.95  # 95% accuracy for invoice number extraction
    TOTAL_MATH_ACCURACY: float = 1.0  # 100% accuracy for total calculations
    MAX_WORKERS: int = Field(default=2, env="MAX_WORKERS")  # Reduced from 5 to 2

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
    REDIS_URL: str = Field(..., env="CELERY_BROKER_URL")   

    # Database Configuration (for potential future use)
    DATABASE_URL: Optional[str] = Field(default=None, env="DATABASE_URL")

    # Celery Configuration
    CELERY_BROKER_URL: str = Field(..., env="CELERY_BROKER_URL")
    CELERY_RESULT_BACKEND: str = Field(..., env="CELERY_RESULT_BACKEND")
    CELERY_WORKER_CONCURRENCY: int = Field(default=2, env="CELERY_WORKER_CONCURRENCY")
    CELERY_WORKER_MAX_TASKS_PER_CHILD: int = Field(default=10, env="CELERY_WORKER_MAX_TASKS_PER_CHILD")
    CELERY_WORKER_PREFETCH_MULTIPLIER: int = Field(default=1, env="CELERY_WORKER_PREFETCH_MULTIPLIER")

    # New Celery Beat Configuration
    CELERY_BEAT_SCHEDULE: dict = Field(default={}, env="CELERY_BEAT_SCHEDULE")
    CELERY_BEAT_MAX_LOOP_INTERVAL: int = Field(default=300, env="CELERY_BEAT_MAX_LOOP_INTERVAL")   

    # Maintenance Task Configuration
    CLEANUP_TEMP_FILES_INTERVAL: int = Field(default=86400, env="CLEANUP_TEMP_FILES_INTERVAL")  # 24 hours in seconds
    CLEANUP_OLD_TASKS_INTERVAL: int = Field(default=604800, env="CLEANUP_OLD_TASKS_INTERVAL")  # 7 days in seconds
    CLEANUP_OLD_TASKS_AGE: int = Field(default=30, env="CLEANUP_OLD_TASKS_AGE")  # 30 days

    # Monitoring Configuration
    CHECK_WORKER_STATUS_INTERVAL: int = Field(default=3600, env="CHECK_WORKER_STATUS_INTERVAL")  # 1 hour in seconds
    CHECK_QUEUE_STATUS_INTERVAL: int = Field(default=900, env="CHECK_QUEUE_STATUS_INTERVAL")  # 15 minutes in seconds
    CHECK_LONG_RUNNING_TASKS_INTERVAL: int = Field(default=300, env="CHECK_LONG_RUNNING_TASKS_INTERVAL")  # 5 minutes in seconds
    LONG_RUNNING_TASK_THRESHOLD: int = Field(default=420, env="LONG_RUNNING_TASK_THRESHOLD")  # 7 minutes in seconds

    # Retry Configuration
    RETRY_FAILED_TASKS_INTERVAL: int = Field(default=1800, env="RETRY_FAILED_TASKS_INTERVAL")  # 30 minutes in seconds
    MAX_TASK_RETRIES: int = Field(default=3, env="MAX_TASK_RETRIES")

    # Logging Configuration
    LOG_LEVEL: str = Field(default="INFO", env="LOG_LEVEL")
    LOG_FILE: str = Field(default="/var/log/app.log", env="LOG_FILE")
    LOG_ROTATION: str = Field(default="500 MB", env="LOG_ROTATION")

    class Config:
        env_file = ".env"
        case_sensitive = True

settings = Settings()

def get_settings() -> Settings:
    return settings
