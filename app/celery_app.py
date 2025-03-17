from celery import Celery
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

@celery_app.task(bind=True)
def process_file_task(self, task_id: str, file_path: str):
    process = psutil.Process()
    logger.info(f"Memory usage at start of task: {process.memory_info().rss / 1024 / 1024} MB")
    with managed_temp_dir() as temp_dir:
        processed_files = []
        try:
            logger.info(f"Starting processing for task {task_id}")
            self.update_state(state='STARTED', meta={'progress': 0, 'message': 'Starting processing'})
            
            processed_files = file_handler.process_upload(file_path)
            logger.info(f"File processed: {file_path}")
            self.update_state(state='PROCESSING', meta={'progress': 20, 'message': 'File processed'})

            loop = asyncio.get_event_loop()
            ocr_results = loop.run_until_complete(ocr_engine.process_documents(processed_files))
            logger.info("OCR completed")
            self.update_state(state='PROCESSING', meta={'progress': 40, 'message': 'OCR completed'})

            extracted_data = loop.run_until_complete(asyncio.gather(*[data_extractor.extract_data(result) for result in ocr_results.values()]))
            logger.info("Data extraction completed")
            self.update_state(state='PROCESSING', meta={'progress': 60, 'message': 'Data extraction completed'})

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
            
        except Exception as e:
            logger.error(f"Error in task {task_id}: {str(e)}", exc_info=True)
            self.update_state(state='FAILURE', meta={'progress': 100, 'message': f'Error: {str(e)}'})
            raise
        finally:
            logger.info(f"Cleaning up for task {task_id}")
            for file in processed_files:
                if os.path.exists(file):
                    os.remove(file)
    logger.info(f"Memory usage at end of task: {process.memory_info().rss / 1024 / 1024} MB")

@celery_app.task(bind=True)
def process_multiple_files_task(self, task_id: str, file_paths: List[str]):
    process = psutil.Process()
    logger.info(f"Memory usage at start of task: {process.memory_info().rss / 1024 / 1024} MB")
    with managed_temp_dir() as temp_dir:
        processed_files = []
        try:
            logger.info(f"Starting processing for task {task_id}")
            self.update_state(state='STARTED', meta={'progress': 0, 'message': 'Starting processing'})
            
            for idx, file_path in enumerate(file_paths):
                processed_files.extend(file_handler.process_upload(file_path))
                progress = (idx + 1) / len(file_paths) * 20
                logger.info(f"Processed file {idx + 1} of {len(file_paths)}: {file_path}")
                self.update_state(state='PROCESSING', meta={'progress': progress, 'message': f'Processed {idx + 1} of {len(file_paths)} files'})

            loop = asyncio.get_event_loop()
            ocr_results = loop.run_until_complete(ocr_engine.process_documents(processed_files))
            logger.info("OCR completed")
            self.update_state(state='PROCESSING', meta={'progress': 40, 'message': 'OCR completed'})

            extracted_data = loop.run_until_complete(asyncio.gather(*[data_extractor.extract_data(result) for result in ocr_results.values()]))
            logger.info("Data extraction completed")
            self.update_state(state='PROCESSING', meta={'progress': 60, 'message': 'Data extraction completed'})

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
            
        except Exception as e:
            logger.error(f"Error in task {task_id}: {str(e)}", exc_info=True)
            self.update_state(state='FAILURE', meta={'progress': 100, 'message': f'Error: {str(e)}'})
            raise
        finally:
            logger.info(f"Cleaning up for task {task_id}")
            for file in processed_files:
                if os.path.exists(file):
                    os.remove(file)
    logger.info(f"Memory usage at end of task: {process.memory_info().rss / 1024 / 1024} MB")

celery_app.conf.task_routes = {
    'app.celery_app.process_file_task': {'queue': 'single_file'},
    'app.celery_app.process_multiple_files_task': {'queue': 'multiple_files'},
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
    broker_url=settings.CELERY_BROKER_URL
)

if __name__ == '__main__':
    celery_app.start()
