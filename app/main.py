from fastapi import FastAPI, File, UploadFile, HTTPException, BackgroundTasks, Depends, Request, status
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.security import APIKeyHeader, HTTPBasic, HTTPBasicCredentials
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import List, Optional
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
from app.celery_app import process_file_task, process_multiple_files_task
from celery.result import AsyncResult

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

file_handler = FileHandler()

api_key_header = APIKeyHeader(name="X-API-Key")

class ProcessingRequest(BaseModel):
    task_id: str

class ProcessingResponse(BaseModel):
    task_id: str
    status: ProcessingStatus

processing_tasks = {}

def get_api_key(api_key: str = Depends(api_key_header)):
    if api_key != settings.X_API_KEY:  
        raise HTTPException(status_code=403, detail="Could not validate API key")
    return api_key

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

@app.post("/upload/", response_model=ProcessingRequest)
async def upload_files(files: List[UploadFile] = File(...), api_key: str = Depends(get_api_key)):
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
            logger.info(f"Processing single file: {file_paths[0]}")
            celery_task = process_file_task.delay(task_id, file_paths[0], temp_dir)
        else:
            logger.info(f"Processing multiple files: {file_paths}")
            celery_task = process_multiple_files_task.delay(task_id, file_paths, temp_dir)
        
        processing_tasks[task_id] = ProcessingStatus(status="Processing", progress=0, message="Processing started")
        logger.info(f"Task {task_id} queued for processing")
        
        return ProcessingRequest(task_id=task_id)
    except Exception as e:
        logger.error(f"Unexpected error during file upload: {str(e)}", exc_info=True)
        shutil.rmtree(temp_dir)  # Clean up in case of error
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred during file upload: {str(e)}")

@app.get("/status/{task_id}", response_model=ProcessingResponse)
async def get_processing_status(task_id: str, api_key: str = Depends(get_api_key)):
    if task_id not in processing_tasks:
        raise HTTPException(status_code=404, detail="Task not found")
    
    celery_task = AsyncResult(task_id)
    if celery_task.state == 'PENDING':
        status = "Queued"
    elif celery_task.state == 'STARTED':
        status = "Processing"
    elif celery_task.state == 'SUCCESS':
        status = "Completed"
    else:
        status = "Failed"
    
    progress = celery_task.info.get('progress', 0) if celery_task.info else 0
    message = celery_task.info.get('message', '') if celery_task.info else ''
    
    return ProcessingResponse(task_id=task_id, status=ProcessingStatus(status=status, progress=progress, message=message))

@app.get("/download/{task_id}")
async def download_results(task_id: str, format: str = "csv", api_key: str = Depends(get_api_key)):
    if task_id not in processing_tasks:
        raise HTTPException(status_code=404, detail="Task not found")
    
    celery_task = AsyncResult(task_id)
    if celery_task.state != 'SUCCESS':
        raise HTTPException(status_code=400, detail="Processing not completed")
    
    temp_dir = celery_task.info.get('temp_dir', tempfile.gettempdir())
    if format.lower() == "csv":
        file_path = os.path.join(temp_dir, f"{task_id}_invoices.csv")
        media_type = "text/csv"
    elif format.lower() == "excel":
        file_path = os.path.join(temp_dir, f"{task_id}_invoices.xlsx")
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
    
    celery_task = AsyncResult(task_id)
    if celery_task.state != 'SUCCESS':
        raise HTTPException(status_code=400, detail="Processing not completed")
    
    validation_results = celery_task.info.get('validation_results', {})
    return validation_results

@app.get("/anomalies/{task_id}")
async def get_anomalies(task_id: str, api_key: str = Depends(get_api_key)):
    if task_id not in processing_tasks:
        raise HTTPException(status_code=404, detail="Task not found")
    
    celery_task = AsyncResult(task_id)
    if celery_task.state != 'SUCCESS':
        raise HTTPException(status_code=400, detail="Processing not completed")
    
    anomalies = celery_task.info.get('anomalies', [])
    return anomalies

@app.get("/health")
async def health_check():
    return {"status": "healthy"}
 
security = HTTPBasic()

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
    logger.info("Application is starting up")

@app.on_event("shutdown")
async def shutdown_event():
    logger.info("Application is shutting down")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
