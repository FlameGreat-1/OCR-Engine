import os
import zipfile
import magic
from fastapi import UploadFile, HTTPException
from typing import List, Dict, Union
import fitz  # PyMuPDF
import io
import uuid
import asyncio
from concurrent.futures import ThreadPoolExecutor
from app.config import settings
from app.models import FileUpload
from PIL import Image
import logging
from tenacity import retry, stop_after_attempt, wait_exponential

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class FileProcessingError(Exception):
    """Custom exception for file processing errors"""
    pass

class FileHandler:
    def __init__(self, upload_dir: str = "/tmp/invoice_uploads"):
        self.upload_dir = upload_dir
        os.makedirs(self.upload_dir, exist_ok=True)
        self.executor = ThreadPoolExecutor(max_workers=settings.MAX_WORKERS)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
    async def save_upload(self, file: UploadFile) -> FileUpload:
        try:
            content_type = await self._get_content_type(file)
            if content_type not in settings.ALLOWED_EXTENSIONS:
                raise HTTPException(status_code=400, detail=f"Unsupported file type: {content_type}")
            
            file_path, file_size = await self._save_file(file)
            return FileUpload(filename=file_path, content_type=content_type, file_size=file_size)
        except Exception as e:
            logger.error(f"Error saving upload: {str(e)}")
            raise HTTPException(status_code=500, detail="Internal server error while saving file")

    async def _get_content_type(self, file: UploadFile) -> str:
        try:
            chunk = await file.read(1024)
            await file.seek(0)
            return magic.from_buffer(chunk, mime=True)
        except Exception as e:
            logger.error(f"Error determining content type: {str(e)}")
            raise FileProcessingError("Unable to determine file type")

    async def _save_file(self, file: UploadFile) -> tuple:
        file_path = os.path.join(self.upload_dir, f"{uuid.uuid4()}_{file.filename}")
        file_size = 0
        try:
            with open(file_path, "wb") as buffer:
                while chunk := await file.read(8192):
                    file_size += len(chunk)
                    if file_size > settings.MAX_UPLOAD_SIZE:
                        raise ValueError("File size exceeds the maximum allowed size")
                    buffer.write(chunk)
            return file_path, file_size
        except Exception as e:
            if os.path.exists(file_path):
                os.remove(file_path)
            logger.error(f"Error saving file: {str(e)}")
            raise FileProcessingError(f"Unable to save file: {str(e)}")

    async def process_uploads(self, file_uploads: List[FileUpload]) -> List[Dict[str, any]]:
        tasks = [self.process_upload(file_upload) for file_upload in file_uploads]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        processed_results = []
        for result in results:
            if isinstance(result, Exception):
                logger.error(f"Error processing upload: {str(result)}")
            else:
                processed_results.extend(result)
        return processed_results

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
    async def process_upload(self, file_upload: FileUpload) -> List[Dict[str, any]]:
        try:
            if file_upload.content_type == 'application/zip':
                return await self._process_zip(file_upload.filename)
            elif file_upload.content_type == 'application/pdf':
                return await self._process_pdf(file_upload.filename)
            else:  # Image files
                return [await self._process_image(file_upload.filename)]
        except Exception as e:
            logger.error(f"Error processing {file_upload.filename}: {str(e)}")
            raise FileProcessingError(f"Unable to process file {file_upload.filename}: {str(e)}")

    async def _process_zip(self, zip_path: str) -> List[Dict[str, any]]:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self.executor, self._process_zip_sync, zip_path)

    def _process_zip_sync(self, zip_path: str) -> List[Dict[str, any]]:
        extracted_files = []
        try:
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                for file_info in zip_ref.infolist():
                    if not file_info.filename.endswith('/'):  # Not a directory
                        with zip_ref.open(file_info) as file:
                            content = file.read()
                            content_type = magic.from_buffer(content, mime=True)
                            if content_type in settings.ALLOWED_EXTENSIONS:
                                if content_type == 'application/pdf':
                                    extracted_files.extend(self._process_pdf_content(file_info.filename, content))
                                else:
                                    extracted_files.append(self._process_image_content(file_info.filename, content))
        except Exception as e:
            logger.error(f"Error processing zip file {zip_path}: {str(e)}")
            raise FileProcessingError(f"Unable to process zip file {zip_path}: {str(e)}")
        return extracted_files

    async def _process_pdf(self, pdf_path: str) -> List[Dict[str, any]]:
        loop = asyncio.get_event_loop()
        try:
            with open(pdf_path, 'rb') as file:
                content = await loop.run_in_executor(self.executor, file.read)
            return await loop.run_in_executor(self.executor, self._process_pdf_content, os.path.basename(pdf_path), content)
        except Exception as e:
            logger.error(f"Error processing PDF {pdf_path}: {str(e)}")
            raise FileProcessingError(f"Unable to process PDF {pdf_path}: {str(e)}")

    def _process_pdf_content(self, filename: str, content: bytes) -> List[Dict[str, any]]:
        pages = []
        try:
            doc = fitz.open(stream=content, filetype="pdf")
            for page_num in range(len(doc)):
                page = doc.load_page(page_num)
                pix = page.get_pixmap()
                img_bytes = pix.tobytes("png")
                pages.append({
                    'filename': f"{filename}_page_{page_num+1}.png",
                    'content': img_bytes,
                    'page_number': page_num + 1,
                    'total_pages': len(doc)
                })
            doc.close()
        except Exception as e:
            logger.error(f"Error processing PDF content {filename}: {str(e)}")
            raise FileProcessingError(f"Unable to process PDF content {filename}: {str(e)}")
        return [{
            'filename': filename,
            'content': content,
            'pages': pages,
            'is_multipage': len(pages) > 1
        }]

    async def _process_image(self, image_path: str) -> Dict[str, any]:
        loop = asyncio.get_event_loop()
        try:
            with open(image_path, 'rb') as file:
                content = await loop.run_in_executor(self.executor, file.read)
            return await loop.run_in_executor(self.executor, self._process_image_content, os.path.basename(image_path), content)
        except Exception as e:
            logger.error(f"Error processing image {image_path}: {str(e)}")
            raise FileProcessingError(f"Unable to process image {image_path}: {str(e)}")

    def _process_image_content(self, filename: str, content: bytes) -> Dict[str, any]:
        try:
            with Image.open(io.BytesIO(content)) as img:
                img_format = img.format.lower()
                if img_format not in ['jpeg', 'jpg', 'png']:
                    raise ValueError(f"Unsupported image format: {img_format}")
            return {
                'filename': filename,
                'content': content,
                'pages': [{
                    'filename': filename,
                    'content': content,
                    'page_number': 1,
                    'total_pages': 1
                }],
                'is_multipage': False
            }
        except Exception as e:
            logger.error(f"Error processing image content {filename}: {str(e)}")
            raise FileProcessingError(f"Unable to process image content {filename}: {str(e)}")

    async def clean_up(self, file_path: str):
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(self.executor, self._clean_up_sync, file_path)

    def _clean_up_sync(self, file_path: str):
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
        except OSError as e:
            logger.error(f"Error deleting file {file_path}: {str(e)}")
            raise FileProcessingError(f"Unable to delete file {file_path}: {str(e)}")

file_handler = FileHandler()
