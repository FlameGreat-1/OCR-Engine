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

celery_app = Celery('invoice_processing', broker=settings.CELERY_BROKER_URL)

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

@celery_app.task(bind=True, soft_time_limit=420, time_limit=480)
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

        chunk_size = 10  # Adjust based on your needs
        chunks = [processed_files[i:i + chunk_size] for i in range(0, len(processed_files), chunk_size)]
        
        partial_process_chunk = partial(process_chunk, task_id=task_id, temp_dir=temp_dir)
        chunk_results = group(celery_app.task(partial_process_chunk).s(chunk) for chunk in chunks)()
        extracted_data = [item for sublist in chunk_results.get() for item in sublist]

        logger.info("OCR and Data extraction completed")
        self.update_state(state='PROCESSING', meta={'progress': 60, 'message': 'OCR and Data extraction completed'})

        validation_results = invoice_validator.validate_invoice_batch(extracted_data)
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
        self.update_state(state='SUCCESS', meta={
            'progress': 100, 
            'message': 'Processing completed',
            'csv_path': csv_path,
            'excel_path': excel_path,
            'total_invoices': len(validated_data),
            'flagged_invoices': len(flagged_invoices)
        })
        
    except SoftTimeLimitExceeded:
        logger.error(f"Task {task_id} exceeded time limit")
        self.update_state(state='FAILURE', meta={'progress': 100, 'message': 'Task exceeded time limit'})
        raise
    except Exception as e:
        logger.error(f"Error in task {task_id}: {str(e)}", exc_info=True)
        self.update_state(state='FAILURE', meta={'progress': 100, 'message': f'Error: {str(e)}'})
        raise
    finally:
        logger.info(f"Cleaning up for task {task_id}")
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)
            logger.info(f"Temporary directory removed: {temp_dir}")
    logger.info(f"Memory usage at end of task: {process.memory_info().rss / 1024 / 1024} MB")

@celery_app.task(bind=True, soft_time_limit=7200, time_limit=7260)
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

        chunk_size = 5  
        chunks = [processed_files[i:i + chunk_size] for i in range(0, len(processed_files), chunk_size)]
        
        partial_process_chunk = partial(process_chunk, task_id=task_id, temp_dir=temp_dir)
        chunk_results = group(celery_app.task(partial_process_chunk).s(chunk) for chunk in chunks)()
        extracted_data = [item for sublist in chunk_results.get() for item in sublist]

        logger.info("OCR and Data extraction completed")
        self.update_state(state='PROCESSING', meta={'progress': 60, 'message': 'OCR and Data extraction completed'})

        validation_results = invoice_validator.validate_invoice_batch(extracted_data)
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
        self.update_state(state='SUCCESS', meta={
            'progress': 100, 
            'message': 'Processing completed',
            'csv_path': csv_path,
            'excel_path': excel_path,
            'total_invoices': len(validated_data),
            'flagged_invoices': len(flagged_invoices)
        })
        
    except SoftTimeLimitExceeded:
        logger.error(f"Task {task_id} exceeded time limit")
        self.update_state(state='FAILURE', meta={'progress': 100, 'message': 'Task exceeded time limit'})
        raise
    except Exception as e:
        logger.error(f"Error in task {task_id}: {str(e)}", exc_info=True)
        self.update_state(state='FAILURE', meta={'progress': 100, 'message': f'Error: {str(e)}'})
        raise
    finally:
        logger.info(f"Cleaning up for task {task_id}")
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)
            logger.info(f"Temporary directory removed: {temp_dir}")
    logger.info(f"Memory usage at end of task: {process.memory_info().rss / 1024 / 1024} MB")

celery_app.conf.task_routes = {
    'app.celery_app.process_file_task': {'queue': 'single_file'},
    'app.celery_app.process_multiple_files_task': {'queue': 'multiple_files'},
}

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
    task_time_limit=480,  # 8 minutes
    task_soft_time_limit=420,  # 7 minutes
    worker_max_memory_per_child=1000000,  # 1GB, adjust as needed
    beat_max_loop_interval=300,  # 5 minutes
)

if __name__ == '__main__':
    celery_app.start()
