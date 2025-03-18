from app.celery_app import celery_app
from app.utils.maintenance import (
    cleanup_temp_files,
    cleanup_old_tasks,
    check_worker_status,
    check_queue_status,
    check_long_running_tasks,
    retry_failed_tasks
)

# Register the periodic tasks
@celery_app.task
def cleanup_temp_files_task():
    cleanup_temp_files()

@celery_app.task
def cleanup_old_tasks_task(days):
    cleanup_old_tasks(days)

@celery_app.task
def check_worker_status_task():
    check_worker_status()

@celery_app.task
def check_queue_status_task():
    check_queue_status()

@celery_app.task
def retry_failed_tasks_task():
    retry_failed_tasks()

@celery_app.task
def check_long_running_tasks_task(threshold_seconds):
    check_long_running_tasks(threshold_seconds)

