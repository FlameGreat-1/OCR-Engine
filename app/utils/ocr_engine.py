import asyncio
from typing import List, Dict
import io
import logging
from google.cloud import vision, documentai_v1 as documentai
import cv2
import numpy as np
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor
from app.config import settings
from app.models import ProcessingStatus, Invoice, Vendor, Address, InvoiceItem
from decimal import Decimal
from datetime import datetime
import aioredis
from tenacity import retry, stop_after_attempt, wait_exponential
import os
import hashlib 
import time

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class OCREngine:
    def __init__(self):
        self.gcv_client = vision.ImageAnnotatorClient()
        self.docai_client = documentai.DocumentProcessorServiceClient(
               client_options={"api_endpoint": "eu-documentai.googleapis.com"}
         )

        self.redis = None
        self.thread_executor = ThreadPoolExecutor(max_workers=settings.MAX_WORKERS)
        self.process_executor = ProcessPoolExecutor(max_workers=settings.MAX_WORKERS)

    async def initialize(self):
        self.redis = await aioredis.from_url(settings.REDIS_URL)

    async def process_documents(self, documents: List[Dict[str, any]]) -> Dict[str, Dict]:
        results = {}
        total_documents = len(documents)
        start_time = time.time()
        
        async def process_batch(batch):
            batch_results = await asyncio.gather(*[self._process_document(doc) for doc in batch])
            return {doc['filename']: result for doc, result in zip(batch, batch_results)}

        optimal_batch_size = max(1, min(settings.BATCH_SIZE, total_documents // settings.MAX_WORKERS))
        batches = [documents[i:i+optimal_batch_size] for i in range(0, len(documents), optimal_batch_size)]
        
        for index, batch in enumerate(batches, 1):
            batch_results = await process_batch(batch)
            results.update(batch_results)
            
            processed_count = min(index * optimal_batch_size, total_documents)
            status = await self.update_processing_status(total_documents, processed_count)
            logger.info(f"Processing status: {status.dict()}")

        end_time = time.time()
        processing_time = end_time - start_time
        logger.info(f"Total processing time: {processing_time:.2f} seconds")
        logger.info(f"Average time per document: {processing_time/total_documents:.2f} seconds")

        return results
    
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
    async def _process_document(self, document):
        try:
            if isinstance(document, str):
                file_path = document
                file_name = os.path.basename(file_path)
                
                with open(file_path, 'rb') as f:
                    content = f.read()
                
                document = {
                    'filename': file_name,
                    'content': content,
                    'is_multipage': False  
                }
            
            if self.redis:
                content_hash = hashlib.md5(document['content']).hexdigest()
                cache_key = f"ocr:{content_hash}"
                cached_result = await self.redis.get(cache_key)
                
                if cached_result:
                    logger.info(f"Cache hit for document: {document['filename']}")
                    return eval(cached_result)
            else:
                logger.warning("Redis not initialized, skipping cache check")

            logger.info(f"Processing document: {document['filename']}")
            start_time = time.time()

            if document['is_multipage']:
                ocr_result = await self._process_multipage(document)
            else:
                ocr_result = await self._process_single_page(document)

            extracted_data = await self._extract_structured_data(ocr_result)
            
            if self.redis:
                content_hash = hashlib.md5(document['content']).hexdigest()
                cache_key = f"ocr:{content_hash}" 
                await self.redis.set(cache_key, str(extracted_data), ex=86400)

            end_time = time.time()
            processing_time = end_time - start_time
            logger.info(f"Document {document['filename']} processed in {processing_time:.2f} seconds")

            return extracted_data
        except Exception as e:
            if isinstance(document, str):
                logger.error(f"Error processing file {os.path.basename(document)}: {str(e)}")
            else:
                logger.error(f"Error processing {document['filename']}: {str(e)}")
            raise  # This will trigger the retry mechanism.
    
    async def _process_multipage(self, document: Dict[str, any]) -> Dict:
        results = await asyncio.gather(*[self._process_single_page({'content': page['content'], 'filename': f"{document['filename']}_page{i}"}) for i, page in enumerate(document['pages'], 1)])
        return {
            "pages": results,
            "is_multipage": True,
            "num_pages": len(results)
        }
 
    async def _process_single_page(self, document: Dict[str, any]) -> Dict:
        image_bytes = document['content']
        image_name = document['filename']
        
        try:
            preprocessed_image = await self._preprocess_image(image_bytes)
            ocr_result, layout_result = await asyncio.gather(
                self._process_with_gcv(image_name, preprocessed_image),
                self._analyze_layout(preprocessed_image)
            )
            ocr_result.update(layout_result)
      
            ocr_result['content'] = image_bytes
            return ocr_result
        except Exception as e:
            logger.error(f"Error in single page processing for {image_name}: {str(e)}")
            raise  # This will trigger the retry mechanism
    
    async def _preprocess_image(self, image_bytes: bytes) -> bytes:
        return await asyncio.get_event_loop().run_in_executor(self.process_executor, self._preprocess_image_sync, image_bytes)

    @staticmethod
    def _preprocess_image_sync(image_bytes: bytes) -> bytes:
        nparr = np.frombuffer(image_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        denoised = cv2.fastNlMeansDenoising(gray)
        _, threshold = cv2.threshold(denoised, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        is_success, buffer = cv2.imencode(".png", threshold)
        if not is_success:
            raise ValueError("Failed to encode preprocessed image")
        return buffer.tobytes()

    async def _process_with_gcv(self, image_name: str, image_bytes: bytes) -> Dict:
        image = vision.Image(content=image_bytes)
        try:
            response = await asyncio.to_thread(self.gcv_client.document_text_detection, image)
            document = response.full_text_annotation

            words = []
            boxes = []
            for page in document.pages:
                for block in page.blocks:
                    for paragraph in block.paragraphs:
                        for word in paragraph.words:
                            word_text = ''.join([symbol.text for symbol in word.symbols])
                            words.append(word_text)
                            vertices = [(vertex.x, vertex.y) for vertex in word.bounding_box.vertices]
                            boxes.append(vertices)

            return {
                "words": words,
                "boxes": boxes,
                "is_multipage": False,
                "num_pages": 1
            }
        except Exception as e:
            logger.error(f"Google Cloud Vision API error for {image_name}: {str(e)}")
            raise  # This will trigger the retry mechanism

    async def _analyze_layout(self, image_bytes: bytes) -> Dict:
        image = vision.Image(content=image_bytes)
        try:
            response = await asyncio.to_thread(self.gcv_client.document_text_detection, image)
            return self._parse_layout(response)
        except Exception as e:
            logger.error(f"Layout analysis error: {str(e)}")
            raise  # This will trigger the retry mechanism

    def _parse_layout(self, response) -> Dict:
        layout = {"tables": [], "key_value_pairs": []}
        for page in response.full_text_annotation.pages:
            for block in page.blocks:
                if block.block_type == vision.Block.BlockType.TABLE:
                    table = self._extract_table(block)
                    layout["tables"].append(table)
                elif block.block_type == vision.Block.BlockType.TEXT:
                    key_value_pair = self._extract_key_value_pair(block)
                    if key_value_pair:
                        layout["key_value_pairs"].append(key_value_pair)
        return layout

    def _extract_table(self, block) -> List[List[str]]:
        table = []
        
        for paragraph in block.paragraphs:
            table_row = []
            for word in paragraph.words:
                cell_text = ''.join([symbol.text for symbol in word.symbols])
                table_row.append(cell_text)
            if table_row:  # Only add non-empty rows
                table.append(table_row)
        return table

    def _extract_key_value_pair(self, block) -> Dict[str, str]:
        
        text = ""
        for paragraph in block.paragraphs:
            paragraph_text = ''.join([''.join([symbol.text for symbol in word.symbols]) 
                                     for word in paragraph.words])
            text += paragraph_text + " "
        
        text = text.strip()
        if ':' in text:
            key, value = text.split(':', 1)
            return {key.strip(): value.strip()}
        return None    

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
    async def _extract_structured_data(self, ocr_result: Dict) -> Dict:
        try:
            if 'content' not in ocr_result:
                text_content = " ".join(ocr_result.get('words', []))
                content = text_content.encode('utf-8')
            else:
                content = ocr_result['content']
            
            # Extract the processor path correctly from the full URL
            processor_name = settings.DOCAI_PROCESSOR_NAME
            if "https://" in processor_name:
                # Extract just the path portion without the https:// prefix
                processor_name = processor_name.split("/v1/")[1]
            
            # Determine the correct MIME type based on the file extension
            filename = ocr_result.get('filename', '')
            mime_type = "application/pdf"  # Default
            
            if filename.lower().endswith(('.jpg', '.jpeg')):
                mime_type = "image/jpeg"
            elif filename.lower().endswith('.png'):
                mime_type = "image/png"
            elif filename.lower().endswith('.zip'):
                mime_type = "application/zip"
            
            # Create the request with the correct format
            request = documentai.ProcessRequest(
                name=processor_name,
                raw_document=documentai.RawDocument(
                    content=content,
                    mime_type=mime_type
                )
            )
            
            # Process the document
            response = await asyncio.to_thread(
                self.docai_client.process_document,
                request=request
            )
            
            # Parse the response
            invoice = self._parse_docai_response(response.document)
            return invoice.dict()
        except Exception as e:
            logger.error(f"Error extracting structured data: {str(e)}")
            raise  # This will trigger the retry mechanism   
                      
    def _parse_docai_response(self, document) -> Invoice:
        entities = {e.type_: e.mention_text for e in document.entities}
        
        vendor = Vendor(
            name=entities.get('supplier_name', ''),
            address=Address(
                street=entities.get('supplier_address', ''),
                city=entities.get('supplier_city', ''),
                state=entities.get('supplier_state', ''),
                country=entities.get('supplier_country', ''),
                postal_code=entities.get('supplier_zip', '')
            )
        )

        items = []
        for table in document.pages[0].tables:
            for row in table.body_rows:
                item = InvoiceItem(
                    description=row.cells[0].layout.text_anchor.content,
                    quantity=int(row.cells[1].layout.text_anchor.content),
                    unit_price=Decimal(row.cells[2].layout.text_anchor.content),
                    total=Decimal(row.cells[3].layout.text_anchor.content)
                )
                items.append(item)

        return Invoice(
            filename=document.uri,
            invoice_number=entities.get('invoice_id', ''),
            vendor=vendor,
            invoice_date=datetime.strptime(entities.get('invoice_date', ''), '%Y-%m-%d'),
            grand_total=Decimal(entities.get('total_amount', '0')),
            taxes=Decimal(entities.get('total_tax_amount', '0')),
            final_total=Decimal(entities.get('total_amount', '0')),
            items=items,
            pages=len(document.pages)
        )           

    async def update_processing_status(self, total_documents: int, processed_documents: int) -> ProcessingStatus:
        progress = (processed_documents / total_documents) * 100
        return ProcessingStatus(
            status="Processing" if processed_documents < total_documents else "Complete",
            progress=progress,
            message=f"Processed {processed_documents} out of {total_documents} documents"
        )

    async def cleanup(self):
        self.thread_executor.shutdown(wait=True)
        self.process_executor.shutdown(wait=True)
        if self.redis:
            await self.redis.close()
        
ocr_engine = OCREngine()

# Initialization function to be called at application startup
async def initialize_ocr_engine():
    await ocr_engine.initialize()

# Cleanup function to be called at application shutdown
async def cleanup_ocr_engine():
    await ocr_engine.cleanup()
