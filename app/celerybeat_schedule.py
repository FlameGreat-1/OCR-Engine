from celery.schedules import crontab
from app.celery_app import celery_app
from app.celery_app import celery_app
from app.utils.maintenance import (
    cleanup_temp_files,
    cleanup_old_tasks,
    check_worker_status,
    check_queue_status,
    check_long_running_tasks,
    retry_failed_tasks
)


celery_app.conf.beat_schedule = {
    'cleanup-temp-files-daily': {
        'task': 'app.utils.maintenance.cleanup_temp_files',
        'schedule': crontab(hour=1, minute=0),
        'args': (),
    },
    'cleanup-old-tasks-weekly': {
        'task': 'app.utils.maintenance.cleanup_old_tasks',
        'schedule': crontab(day_of_week=0, hour=2, minute=0),
        'args': (30,),
    },
    'check-worker-status-hourly': {
        'task': 'app.utils.maintenance.check_worker_status',
        'schedule': crontab(minute=0),
        'args': (),
    },
    'check-queue-status-every-15-minutes': {
        'task': 'app.utils.maintenance.check_queue_status',
        'schedule': crontab(minute='*/15'),
        'args': (),
    },
    'retry-failed-tasks-every-30-minutes': {
        'task': 'app.utils.maintenance.retry_failed_tasks',
        'schedule': crontab(minute='*/30'),
        'args': (),
    },
    'check-long-running-tasks-every-5-minutes': {
        'task': 'app.utils.maintenance.check_long_running_tasks',
        'schedule': crontab(minute='*/5'),
        'args': (420,),
    },
}

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

# configuration for Celery Beat
celery_app.conf.beat_max_loop_interval = 300  # 5 minutes
