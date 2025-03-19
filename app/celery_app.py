from celery import Celery, group, chord
from celery.exceptions import SoftTimeLimitExceeded
from celery.schedules import crontab
from app.config import settings
from app.utils.file_handler import FileHandler
from app.utils.ocr_engine import ocr_engine
from app.utils.data_extractor import data_extractor
from app.utils.validator import invoice_validator, flag_anomalies
from app.utils.exporter import export_invoices
import os
import tempfile
from typing import List
import shutil
import logging
from contextlib import contextmanager
import asyncio
import psutil
from functools import partial

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize Celery with explicit result backend and broker
celery_app = Celery(
    'invoice_processing',
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND
)

file_handler = FileHandler()

@contextmanager
def managed_temp_dir():
    temp_dir = tempfile.mkdtemp()
    try:
        yield temp_dir
    finally:
        shutil.rmtree(temp_dir)

def process_chunk(chunk, task_id, temp_dir):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    try:
        ocr_results = loop.run_until_complete(ocr_engine.process_documents(chunk))
        extracted_data = loop.run_until_complete(asyncio.gather(*[data_extractor.extract_data(result) for result in ocr_results.values()]))
        return extracted_data
    finally:
        loop.close()

@celery_app.task(bind=True, name='process_file_task', soft_time_limit=420, time_limit=480)
def process_file_task(self, task_id: str, file_path: str, temp_dir: str):
    process = psutil.Process()
    logger.info(f"Memory usage at start of task: {process.memory_info().rss / 1024 / 1024} MB")
    logger.info(f"Starting process_file_task for task_id: {task_id}, file_path: {file_path}")
    logger.info(f"Current working directory: {os.getcwd()}")
    logger.info(f"File exists: {os.path.exists(file_path)}")

    try:
        logger.info(f"Starting processing for task {task_id}")
        self.update_state(state='STARTED', meta={'progress': 0, 'message': 'Starting processing'})
        
        processed_files = file_handler.process_upload(file_path)
        logger.info(f"File processed: {file_path}")
        self.update_state(state='PROCESSING', meta={'progress': 20, 'message': 'File processed'})

        # Process in the current task instead of creating subtasks for better reliability
        all_extracted_data = []
        total_files = len(processed_files)
        
        for i, file_batch in enumerate(processed_files):
            # Process directly instead of using group
            ocr_results = asyncio.run(ocr_engine.process_documents([file_batch]))
            batch_data = asyncio.run(asyncio.gather(*[data_extractor.extract_data(result) for result in ocr_results.values()]))
            all_extracted_data.extend(batch_data)
            
            # Update progress
            progress = 20 + (i / total_files * 40)
            self.update_state(state='PROCESSING', meta={'progress': progress, 'message': f'Processed {i+1}/{total_files} files'})
            
        logger.info("OCR and Data extraction completed")
        self.update_state(state='PROCESSING', meta={'progress': 60, 'message': 'OCR and Data extraction completed'})

        validation_results = invoice_validator.validate_invoice_batch(all_extracted_data)
        validated_data = [invoice for invoice, _, _ in validation_results]
        validation_warnings = {invoice['invoice_number']: warnings for invoice, _, warnings in validation_results}
        logger.info("Validation completed")
        self.update_state(state='PROCESSING', meta={'progress': 80, 'message': 'Validation completed'})

        flagged_invoices = flag_anomalies(validated_data)
        
        export_data = []
        for invoice in validated_data:
            invoice_data = invoice.dict()
            invoice_data['validation_warnings'] = validation_warnings.get(invoice.invoice_number, [])
            invoice_data['anomaly_flags'] = [flag for flagged in flagged_invoices if flagged['invoice_number'] == invoice.invoice_number for flag in flagged['flags']]
            export_data.append(invoice_data)

        csv_output = export_invoices(export_data, 'csv')
        excel_output = export_invoices(export_data, 'excel')
        
        csv_path = os.path.join(temp_dir, f"{task_id}_invoices.csv")
        excel_path = os.path.join(temp_dir, f"{task_id}_invoices.xlsx")
        
        with open(csv_path, 'wb') as f:
            f.write(csv_output.getvalue())
        with open(excel_path, 'wb') as f:
            f.write(excel_output.getvalue())
        
        logger.info(f"Processing completed for task {task_id}")
        result = {
            'progress': 100, 
            'message': 'Processing completed',
            'csv_path': csv_path,
            'excel_path': excel_path,
            'total_invoices': len(validated_data),
            'flagged_invoices': len(flagged_invoices),
            'status': 'Completed'
        }
        self.update_state(state='SUCCESS', meta=result)
        return result
        
    except SoftTimeLimitExceeded:
        logger.error(f"Task {task_id} exceeded time limit")
        result = {'progress': 100, 'message': 'Task exceeded time limit', 'status': 'Failed'}
        self.update_state(state='FAILURE', meta=result)
        return result
    except Exception as e:
        logger.error(f"Error in task {task_id}: {str(e)}", exc_info=True)
        result = {'progress': 100, 'message': f'Error: {str(e)}', 'status': 'Failed'}
        self.update_state(state='FAILURE', meta=result)
        return result
    finally:
        logger.info(f"Cleaning up for task {task_id}")
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)
            logger.info(f"Temporary directory removed: {temp_dir}")
        logger.info(f"Memory usage at end of task: {process.memory_info().rss / 1024 / 1024} MB")

@celery_app.task(bind=True, name='process_multiple_files_task', soft_time_limit=7200, time_limit=7260)
def process_multiple_files_task(self, task_id: str, file_paths: List[str], temp_dir: str):
    process = psutil.Process()
    logger.info(f"Memory usage at start of task: {process.memory_info().rss / 1024 / 1024} MB")    

    try:
        logger.info(f"Starting processing for task {task_id}")
        self.update_state(state='STARTED', meta={'progress': 0, 'message': 'Starting processing'})
        
        processed_files = []
        for idx, file_path in enumerate(file_paths):
            processed_files.extend(file_handler.process_upload(file_path))
            progress = (idx + 1) / len(file_paths) * 20
            logger.info(f"Processed file {idx + 1} of {len(file_paths)}: {file_path}")
            self.update_state(state='PROCESSING', meta={'progress': progress, 'message': f'Processed {idx + 1} of {len(file_paths)} files'})

        # Process in the current task instead of creating subtasks
        all_extracted_data = []
        total_batches = len(processed_files)
        
        for i, file_batch in enumerate(processed_files):
            # Process directly instead of using group
            ocr_results = asyncio.run(ocr_engine.process_documents([file_batch]))
            batch_data = asyncio.run(asyncio.gather(*[data_extractor.extract_data(result) for result in ocr_results.values()]))
            all_extracted_data.extend(batch_data)
            
            # Update progress
            progress = 20 + (i / total_batches * 40)
            self.update_state(state='PROCESSING', meta={'progress': progress, 'message': f'Processed {i+1}/{total_batches} batches'})

        logger.info("OCR and Data extraction completed")
        self.update_state(state='PROCESSING', meta={'progress': 60, 'message': 'OCR and Data extraction completed'})

        validation_results = invoice_validator.validate_invoice_batch(all_extracted_data)
        validated_data = [invoice for invoice, _, _ in validation_results]
        validation_warnings = {invoice['invoice_number']: warnings for invoice, _, warnings in validation_results}
        logger.info("Validation completed")
        self.update_state(state='PROCESSING', meta={'progress': 80, 'message': 'Validation completed'})

        flagged_invoices = flag_anomalies(validated_data)
        
        export_data = []
        for invoice in validated_data:
            invoice_data = invoice.dict()
            invoice_data['validation_warnings'] = validation_warnings.get(invoice.invoice_number, [])
            invoice_data['anomaly_flags'] = [flag for flagged in flagged_invoices if flagged['invoice_number'] == invoice.invoice_number for flag in flagged['flags']]
            export_data.append(invoice_data)

        csv_output = export_invoices(export_data, 'csv')
        excel_output = export_invoices(export_data, 'excel')
        
        csv_path = os.path.join(temp_dir, f"{task_id}_invoices.csv")
        excel_path = os.path.join(temp_dir, f"{task_id}_invoices.xlsx")
        
        with open(csv_path, 'wb') as f:
            f.write(csv_output.getvalue())
        with open(excel_path, 'wb') as f:
            f.write(excel_output.getvalue())
        
        logger.info(f"Processing completed for task {task_id}")
        result = {
            'progress': 100, 
            'message': 'Processing completed',
            'csv_path': csv_path,
            'excel_path': excel_path,
            'total_invoices': len(validated_data),
            'flagged_invoices': len(flagged_invoices),
            'status': 'Completed'
        }
        self.update_state(state='SUCCESS', meta=result)
        return result
        
    except SoftTimeLimitExceeded:
        logger.error(f"Task {task_id} exceeded time limit")
        result = {'progress': 100, 'message': 'Task exceeded time limit', 'status': 'Failed'}
        self.update_state(state='FAILURE', meta=result)
        return result
    except Exception as e:
        logger.error(f"Error in task {task_id}: {str(e)}", exc_info=True)
        result = {'progress': 100, 'message': f'Error: {str(e)}', 'status': 'Failed'}
        self.update_state(state='FAILURE', meta=result)
        return result
    finally:
        logger.info(f"Cleaning up for task {task_id}")
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)
            logger.info(f"Temporary directory removed: {temp_dir}")
        logger.info(f"Memory usage at end of task: {process.memory_info().rss / 1024 / 1024} MB")

@celery_app.task(name='debug_task')
def debug_task(task_id):
    """Simple task to verify worker and result backend are working"""
    logger.info(f"Debug task running for {task_id}")
    return {'status': 'Completed', 'progress': 100, 'message': 'Debug task completed successfully'}

@celery_app.task
def test_task():
    logger.info("Test task executed successfully")
    return "Success"

# Celery Beat schedule
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

# Celery configuration
celery_app.conf.update(
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone='UTC',
    enable_utc=True,
    worker_concurrency=settings.CELERY_WORKER_CONCURRENCY,
    worker_max_tasks_per_child=settings.CELERY_WORKER_MAX_TASKS_PER_CHILD,
    worker_prefetch_multiplier=settings.CELERY_WORKER_PREFETCH_MULTIPLIER,
    result_backend=settings.CELERY_RESULT_BACKEND,
    broker_url=settings.CELERY_BROKER_URL,
    task_track_started=True,
    task_ignore_result=False,  # Ensure results are stored
    task_time_limit=480,  # 8 minutes
    task_soft_time_limit=420,  # 7 minutes
    worker_max_memory_per_child=1000000,  # 1GB, adjust as needed
    beat_max_loop_interval=300,  # 5 minutes
    task_acks_late=True,  # Only acknowledge tasks after they're completed
    task_default_queue='celery',  # Default queue name
)

# Define task routes - ensure workers are started with these queues
celery_app.conf.task_routes = {
    'process_file_task': {'queue': 'celery'},  # Changed to use default queue
    'process_multiple_files_task': {'queue': 'celery'},  # Changed to use default queue
    'debug_task': {'queue': 'celery'},
    'test_task': {'queue': 'celery'},
}

if __name__ == '__main__':
    celery_app.start()

# When starting the worker, use:
# celery -A app.celery_app worker -Q celery -l info
