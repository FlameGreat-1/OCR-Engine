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
import asyncio
import shutil

celery_app = Celery('invoice_processing', broker=settings.CELERY_BROKER_URL)

file_handler = FileHandler()

@celery_app.task(bind=True)
def process_file_task(self, task_id: str, file_path: str):
    temp_dir = tempfile.mkdtemp()
    try:
        self.update_state(state='STARTED', meta={'progress': 0, 'message': 'Starting processing'})
        
        processed_files = file_handler.process_upload(file_path)
        self.update_state(state='PROCESSING', meta={'progress': 20, 'message': 'File processed'})

        loop = asyncio.get_event_loop()
        ocr_results = loop.run_until_complete(ocr_engine.process_documents(processed_files))
        self.update_state(state='PROCESSING', meta={'progress': 40, 'message': 'OCR completed'})

        extracted_data = loop.run_until_complete(asyncio.gather(*[data_extractor.extract_data(result) for result in ocr_results.values()]))
        self.update_state(state='PROCESSING', meta={'progress': 60, 'message': 'Data extraction completed'})

        validation_results = invoice_validator.validate_invoice_batch(extracted_data)
        validated_data = [invoice for invoice, _, _ in validation_results]
        validation_warnings = {invoice['invoice_number']: warnings for invoice, _, warnings in validation_results}
        self.update_state(state='PROCESSING', meta={'progress': 80, 'message': 'Validation completed'})

        flagged_invoices = flag_anomalies(validated_data)
        
        # Include all invoices, their validation warnings, and anomaly flags in the export
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
        
        self.update_state(state='SUCCESS', meta={
            'progress': 100, 
            'message': 'Processing completed',
            'csv_path': csv_path,
            'excel_path': excel_path,
            'total_invoices': len(validated_data),
            'flagged_invoices': len(flagged_invoices)
        })
        
    except Exception as e:
        self.update_state(state='FAILURE', meta={'progress': 100, 'message': f'Error: {str(e)}'})
        raise
    finally:
        # Clean up temporary files
        for file in processed_files:
            if os.path.exists(file):
                os.remove(file)

@celery_app.task(bind=True)
def process_multiple_files_task(self, task_id: str, file_paths: List[str]):
    temp_dir = tempfile.mkdtemp()
    try:
        self.update_state(state='STARTED', meta={'progress': 0, 'message': 'Starting processing'})
        
        processed_files = []
        for idx, file_path in enumerate(file_paths):
            processed_files.extend(file_handler.process_upload(file_path))
            progress = (idx + 1) / len(file_paths) * 20
            self.update_state(state='PROCESSING', meta={'progress': progress, 'message': f'Processed {idx + 1} of {len(file_paths)} files'})

        loop = asyncio.get_event_loop()
        ocr_results = loop.run_until_complete(ocr_engine.process_documents(processed_files))
        self.update_state(state='PROCESSING', meta={'progress': 40, 'message': 'OCR completed'})

        extracted_data = loop.run_until_complete(asyncio.gather(*[data_extractor.extract_data(result) for result in ocr_results.values()]))
        self.update_state(state='PROCESSING', meta={'progress': 60, 'message': 'Data extraction completed'})

        validation_results = invoice_validator.validate_invoice_batch(extracted_data)
        validated_data = [invoice for invoice, _, _ in validation_results]
        validation_warnings = {invoice['invoice_number']: warnings for invoice, _, warnings in validation_results}
        self.update_state(state='PROCESSING', meta={'progress': 80, 'message': 'Validation completed'})

        flagged_invoices = flag_anomalies(validated_data)
        
        # Include all invoices, their validation warnings, and anomaly flags in the export
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
        
        self.update_state(state='SUCCESS', meta={
            'progress': 100, 
            'message': 'Processing completed',
            'csv_path': csv_path,
            'excel_path': excel_path,
            'total_invoices': len(validated_data),
            'flagged_invoices': len(flagged_invoices)
        })
        
    except Exception as e:
        self.update_state(state='FAILURE', meta={'progress': 100, 'message': f'Error: {str(e)}'})
        raise
    finally:
        # Clean up temporary files
        for file in processed_files:
            if os.path.exists(file):
                os.remove(file)

celery_app.conf.task_routes = {
    'app.celery_app.process_file_task': {'queue': 'single_file'},
    'app.celery_app.process_multiple_files_task': {'queue': 'multiple_files'},
}
