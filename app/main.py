from fastapi import FastAPI, File, UploadFile, HTTPException, BackgroundTasks, Depends, Request, status
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.security import APIKeyHeader, HTTPBasic, HTTPBasicCredentials
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
import asyncio
import tempfile
import os
import uuid
import shutil
import secrets
import logging
from app.config import settings
from app.utils.file_handler import FileHandler
from app.utils.ocr_engine import ocr_engine
from app.utils.data_extractor import data_extractor
from app.utils.validator import invoice_validator, flag_anomalies
from app.utils.exporter import export_invoices
from app.models import Invoice, ProcessingStatus

# Initialize FastAPI app
app = FastAPI(title=settings.PROJECT_NAME, version="1.0.0")

# Add middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.ALLOWED_HOSTS)

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize utilities
file_handler = FileHandler()
api_key_header = APIKeyHeader(name="X-API-Key")
security = HTTPBasic()

# Define models
class ProcessingRequest(BaseModel):
    task_id: str

class ProcessingResponse(BaseModel):
    task_id: str
    status: ProcessingStatus

# Global storage
processing_tasks = {}
direct_results = {}

# Helper functions
def get_api_key(api_key: str = Depends(api_key_header)):
    if api_key != settings.X_API_KEY:  
        raise HTTPException(status_code=403, detail="Could not validate API key")
    return api_key

def get_current_username(credentials: HTTPBasicCredentials = Depends(security)):
    correct_username = secrets.compare_digest(credentials.username, "admin")
    correct_password = secrets.compare_digest(credentials.password, "password")
    if not (correct_username and correct_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username

def get_file_type(filename):
    ext = os.path.splitext(filename)[1].lower()
    if ext == '.pdf':
        return "application/pdf"
    elif ext in ['.jpg', '.jpeg']:
        return "image/jpeg"
    elif ext == '.png':
        return "image/png"
    elif ext == '.zip':
        return "application/zip"
    return None
    
# Processing functions
async def process_file_directly(task_id: str, file_path: str, temp_dir: str):
    logger.info(f"Starting direct processing for task {task_id}")
    
    try:
        processing_tasks[task_id] = ProcessingStatus(status="Processing", progress=0, message="Starting processing")
        
        processed_files = await file_handler.process_upload(file_path)
        logger.info(f"File processed: {file_path}")
        processing_tasks[task_id] = ProcessingStatus(status="Processing", progress=20, message="File processed")
        
        all_extracted_data = []
        total_files = len(processed_files)
        
        for i, file_batch in enumerate(processed_files):
            ocr_results = await ocr_engine.process_documents([file_batch])
            batch_data = await asyncio.gather(*[data_extractor.extract_data(result) for result in ocr_results.values()])
            all_extracted_data.extend(batch_data)
            
            progress = 20 + (i / total_files * 40)
            processing_tasks[task_id] = ProcessingStatus(status="Processing", progress=int(progress), 
                                                        message=f'Processed {i+1}/{total_files} files')
        
        logger.info("OCR and Data extraction completed")
        processing_tasks[task_id] = ProcessingStatus(status="Processing", progress=60, message="OCR and Data extraction completed")
        
        validation_results = invoice_validator.validate_invoice_batch(all_extracted_data)
        validated_data = [invoice for invoice, _, _ in validation_results]
        validation_warnings = {invoice['invoice_number']: warnings for invoice, _, warnings in validation_results}
        
        logger.info("Validation completed")
        processing_tasks[task_id] = ProcessingStatus(status="Processing", progress=80, message="Validation completed")
        
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
            'status': 'Completed',
            'temp_dir': temp_dir,
            'validation_results': validation_warnings,
            'anomalies': flagged_invoices
        }
        
        processing_tasks[task_id] = ProcessingStatus(status="Completed", progress=100, message="Processing completed")
        direct_results[task_id] = result
        
        return result
        
    except Exception as e:
        logger.error(f"Error in direct processing: {str(e)}", exc_info=True)
        processing_tasks[task_id] = ProcessingStatus(status="Failed", progress=100, message=f"Error: {str(e)}")
        direct_results[task_id] = {'status': 'Failed', 'message': str(e)}
        raise

async def process_multiple_files_directly(task_id: str, file_paths: List[str], temp_dir: str):
    logger.info(f"Starting direct processing for multiple files, task {task_id}")
    
    try:
        processing_tasks[task_id] = ProcessingStatus(status="Processing", progress=0, message="Starting processing")
        
        processed_files = []
        for idx, file_path in enumerate(file_paths):
            processed_files.extend(await file_handler.process_upload(file_path))
            progress = (idx + 1) / len(file_paths) * 20
            logger.info(f"Processed file {idx + 1} of {len(file_paths)}: {file_path}")
            processing_tasks[task_id] = ProcessingStatus(status="Processing", progress=int(progress), 
                                                        message=f'Processed {idx + 1} of {len(file_paths)} files')
        
        all_extracted_data = []
        total_batches = len(processed_files)
        
        for i, file_batch in enumerate(processed_files):
            ocr_results = await ocr_engine.process_documents([file_batch])
            batch_data = await asyncio.gather(*[data_extractor.extract_data(result) for result in ocr_results.values()])
            all_extracted_data.extend(batch_data)
            
            progress = 20 + (i / total_batches * 40)
            processing_tasks[task_id] = ProcessingStatus(status="Processing", progress=int(progress), 
                                                        message=f'Processed {i+1}/{total_batches} batches')
        
        logger.info("OCR and Data extraction completed")
        processing_tasks[task_id] = ProcessingStatus(status="Processing", progress=60, message="OCR and Data extraction completed")
        
        validation_results = invoice_validator.validate_invoice_batch(all_extracted_data)
        validated_data = [invoice for invoice, _, _ in validation_results]
        validation_warnings = {invoice['invoice_number']: warnings for invoice, _, warnings in validation_results}
        
        logger.info("Validation completed")
        processing_tasks[task_id] = ProcessingStatus(status="Processing", progress=80, message="Validation completed")
        
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
            'status': 'Completed',
            'temp_dir': temp_dir,
            'validation_results': validation_warnings,
            'anomalies': flagged_invoices
        }
        
        processing_tasks[task_id] = ProcessingStatus(status="Completed", progress=100, message="Processing completed")
        direct_results[task_id] = result
        
        return result
        
    except Exception as e:
        logger.error(f"Error in direct processing: {str(e)}", exc_info=True)
        processing_tasks[task_id] = ProcessingStatus(status="Failed", progress=100, message=f"Error: {str(e)}")
        direct_results[task_id] = {'status': 'Failed', 'message': str(e)}
        raise

# API Endpoints
@app.post("/upload/", response_model=ProcessingRequest)
async def upload_files(files: List[UploadFile] = File(...), api_key: str = Depends(get_api_key), background_tasks: BackgroundTasks = BackgroundTasks()):
    task_id = str(uuid.uuid4())
    processing_tasks[task_id] = ProcessingStatus(status="Queued", progress=0, message="Task queued")
    
    temp_dir = tempfile.mkdtemp()
    file_paths = []

    try:
        for file in files:
            logger.info(f"Processing file: {file.filename}, Content-Type: {file.content_type}")
            file_type = file.content_type or get_file_type(file.filename)
            if not file_type or file_type not in ["application/pdf", "image/jpeg", "image/png", "application/zip"]:
                logger.warning(f"Unsupported file type: {file_type}")
                raise HTTPException(status_code=400, detail=f"Unsupported file type: {file_type}")
            
            file_path = os.path.join(temp_dir, file.filename)
            try:
                with open(file_path, "wb") as buffer:
                    content = await file.read()
                    buffer.write(content)
                file_paths.append(file_path)
                logger.info(f"File saved successfully: {file_path}")
            except IOError as e:
                logger.error(f"Error saving file {file.filename}: {str(e)}")
                raise HTTPException(status_code=500, detail=f"Error saving file {file.filename}")

        if len(files) == 1:
            logger.info(f"Processing single file directly: {file_paths[0]}")
            background_tasks.add_task(process_file_directly, task_id, file_paths[0], temp_dir)
        else:
            logger.info(f"Processing multiple files directly: {file_paths}")
            background_tasks.add_task(process_multiple_files_directly, task_id, file_paths, temp_dir)
        
        processing_tasks[task_id] = ProcessingStatus(status="Processing", progress=0, message="Processing started")
        logger.info(f"Task {task_id} started for direct processing")
        
        return ProcessingRequest(task_id=task_id)
    except Exception as e:
        logger.error(f"Unexpected error during file upload: {str(e)}", exc_info=True)
        shutil.rmtree(temp_dir)
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred during file upload: {str(e)}")

@app.get("/status/{task_id}", response_model=ProcessingResponse)
async def get_processing_status(task_id: str, api_key: str = Depends(get_api_key)):
    if task_id not in processing_tasks:
        raise HTTPException(status_code=404, detail="Task not found")
    
    status_info = processing_tasks[task_id]
    return ProcessingResponse(task_id=task_id, status=status_info)

@app.get("/download/{task_id}")
async def download_results(task_id: str, format: str = "csv", api_key: str = Depends(get_api_key)):
    if task_id not in processing_tasks:
        raise HTTPException(status_code=404, detail="Task not found")
    
    if task_id not in direct_results:
        raise HTTPException(status_code=400, detail="Processing not completed")
    
    result = direct_results[task_id]
    
    if format.lower() == "csv":
        file_path = os.path.join(result.get('temp_dir', tempfile.gettempdir()), f"{task_id}_invoices.csv")
        media_type = "text/csv"
    elif format.lower() == "excel":
        file_path = os.path.join(result.get('temp_dir', tempfile.gettempdir()), f"{task_id}_invoices.xlsx")
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    else:
        raise HTTPException(status_code=400, detail="Invalid format specified")
    
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Result file not found")
    
    return FileResponse(file_path, media_type=media_type, filename=os.path.basename(file_path))

@app.get("/validation/{task_id}")
async def get_validation_results(task_id: str, api_key: str = Depends(get_api_key)):
    if task_id not in processing_tasks:
        raise HTTPException(status_code=404, detail="Task not found")
    
    if task_id not in direct_results:
        raise HTTPException(status_code=400, detail="Processing not completed")
    
    validation_results = direct_results[task_id].get('validation_results', {})
    return validation_results

@app.get("/anomalies/{task_id}")
async def get_anomalies(task_id: str, api_key: str = Depends(get_api_key)):
    if task_id not in processing_tasks:
        raise HTTPException(status_code=404, detail="Task not found")
    
    if task_id not in direct_results:
        raise HTTPException(status_code=400, detail="Processing not completed")
    
    anomalies = direct_results[task_id].get('anomalies', [])
    return anomalies

@app.post("/cancel/{task_id}")
async def cancel_task(task_id: str, api_key: str = Depends(get_api_key)):
    if task_id not in processing_tasks:
        raise HTTPException(status_code=404, detail="Task not found")
    
    status_info = processing_tasks[task_id]
    if status_info.status in ['Queued', 'Processing']:
        processing_tasks[task_id] = ProcessingStatus(status="Cancelled", progress=0, message="Task cancelled by user")
        return {"status": "Task cancelled successfully"}
    elif status_info.status in ['Completed', 'Failed']:
        return {"status": "Task already completed or failed, cannot cancel"}
    else:
        return {"status": "Unable to cancel task, unknown state"}

@app.get("/check-task/{task_id}")
def check_task(task_id: str):
    if task_id not in processing_tasks:
        raise HTTPException(status_code=404, detail="Task not found")
    
    status_info = processing_tasks[task_id]
    return {
        "task_id": task_id,
        "status": status_info.status,
        "progress": status_info.progress,
        "message": status_info.message
    }

@app.get("/health")
async def health_check():
    return {"status": "healthy"}

@app.get("/api-key")
def read_api_key(username: str = Depends(get_current_username)):
    return {"api_key": settings.X_API_KEY}
    
# Set up templates and static files
templates = Jinja2Templates(directory="template")

app.mount("/static", StaticFiles(directory="template"), name="static")

@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    return templates.TemplateResponse("testing_ui.html", {"request": request})

@app.on_event("startup")
async def startup_event():
    try:
        logger.info("Application is starting up")
        try:
            await initialize_ocr_engine()
            logger.info("OCR engine initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize OCR engine: {str(e)}")
    except Exception as e:
        logger.error(f"Error during application startup: {str(e)}")

@app.on_event("shutdown")
async def shutdown_event():
    logger.info("Application is shutting down")
    await cleanup_ocr_engine()  
    
if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 10000))  
    uvicorn.run(app, host="0.0.0.0", port=port)



