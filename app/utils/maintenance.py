import os
import shutil
from datetime import datetime, timedelta
from celery.result import AsyncResult
from app.celery_app import celery_app
import logging
from celery.app.control import Control

logger = logging.getLogger(__name__)

# Cleanup functions

def cleanup_temp_files():
    """
    Clean up temporary files older than 24 hours in the temporary directory.
    """
    temp_dir = '/tmp'  # Adjust this path if your temp directory is different
    current_time = datetime.now()
    
    for filename in os.listdir(temp_dir):
        file_path = os.path.join(temp_dir, filename)
        if os.path.isfile(file_path):
            file_modified = datetime.fromtimestamp(os.path.getmtime(file_path))
            if current_time - file_modified > timedelta(hours=24):
                try:
                    os.remove(file_path)
                    logger.info(f"Deleted old temporary file: {file_path}")
                except Exception as e:
                    logger.error(f"Error deleting file {file_path}: {str(e)}")

def cleanup_old_tasks(days):
    """
    Clean up old task results from the result backend older than the specified number of days.
    
    :param days: Number of days to keep task results
    """
    cutoff_date = datetime.now() - timedelta(days=days)
    
    # Get all task IDs from the result backend
    inspector = celery_app.control.inspect()
    active_tasks = inspector.active()
    reserved_tasks = inspector.reserved()
    
    all_task_ids = set()
    if active_tasks:
        all_task_ids.update([task['id'] for tasks in active_tasks.values() for task in tasks])
    if reserved_tasks:
        all_task_ids.update([task['id'] for tasks in reserved_tasks.values() for task in tasks])
    
    # Iterate through all task IDs and remove old ones
    for task_id in all_task_ids:
        result = AsyncResult(task_id)
        if result.date_done and result.date_done < cutoff_date:
            try:
                result.forget()
                logger.info(f"Cleaned up old task result: {task_id}")
            except Exception as e:
                logger.error(f"Error cleaning up task {task_id}: {str(e)}")

# Monitoring functions

def check_worker_status():
    """
    Check the status of all Celery workers and log their state.
    """
    control = Control(celery_app)
    try:
        status = control.inspect().ping()
        if not status:
            logger.error("No workers responded to ping.")
            return
        
        for worker, response in status.items():
            if response.get('ok') == 'pong':
                logger.info(f"Worker {worker} is alive and responding.")
            else:
                logger.warning(f"Worker {worker} is not responding properly.")
    except Exception as e:
        logger.error(f"Error checking worker status: {str(e)}")

def check_queue_status():
    """
    Check the status of Celery queues and log the number of tasks in each.
    """
    try:
        inspector = celery_app.control.inspect()
        active_queues = inspector.active_queues()
        
        if not active_queues:
            logger.warning("No active queues found.")
            return
        
        for worker, queues in active_queues.items():
            for queue in queues:
                queue_name = queue['name']
                task_count = len(inspector.reserved().get(worker, []))
                logger.info(f"Worker {worker}, Queue {queue_name}: {task_count} tasks pending.")
    except Exception as e:
        logger.error(f"Error checking queue status: {str(e)}")

def check_long_running_tasks(threshold_seconds):
    """
    Check for tasks that have been running longer than the specified threshold.
    
    :param threshold_seconds: Number of seconds to consider a task as long-running
    """
    try:
        inspector = celery_app.control.inspect()
        active_tasks = inspector.active()
        
        if not active_tasks:
            logger.info("No active tasks found.")
            return
        
        for worker, tasks in active_tasks.items():
            for task in tasks:
                task_id = task['id']
                runtime = task['time_start']
                if runtime and (datetime.now() - runtime).total_seconds() > threshold_seconds:
                    logger.warning(f"Long-running task detected: {task_id} on worker {worker}, "
                                   f"running for {(datetime.now() - runtime).total_seconds()} seconds.")
    except Exception as e:
        logger.error(f"Error checking long-running tasks: {str(e)}")

# Retry function

def retry_failed_tasks():
    """
    Retry tasks that have failed, up to a maximum of 3 retries.
    """
    try:
        inspector = celery_app.control.inspect()
        reserved_tasks = inspector.reserved()
        
        if not reserved_tasks:
            logger.info("No reserved tasks found to retry.")
            return
        
        for worker, tasks in reserved_tasks.items():
            for task in tasks:
                task_id = task['id']
                result = AsyncResult(task_id)
                
                if result.status == 'FAILURE':
                    retries = task.get('retries', 0)
                    if retries < 3:
                        try:
                            celery_app.send_task(task['name'], args=task['args'], kwargs=task['kwargs'],
                                                 task_id=task_id, countdown=60*(retries+1))
                            logger.info(f"Retrying failed task: {task_id}, retry count: {retries + 1}")
                        except Exception as e:
                            logger.error(f"Error retrying task {task_id}: {str(e)}")
                    else:
                        logger.warning(f"Task {task_id} has failed after 3 retries. Manual intervention may be required.")
    except Exception as e:
        logger.error(f"Error in retry_failed_tasks: {str(e)}")
