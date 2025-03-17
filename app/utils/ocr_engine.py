import asyncio
from typing import List, Dict
import io
import logging
from google.cloud import vision, documentai_v1 as documentai
import cv2
import numpy as np
from concurrent.futures import ThreadPoolExecutor
from app.config import settings
from app.models import ProcessingStatus, Invoice, Vendor, Address, InvoiceItem
from decimal import Decimal
from datetime import datetime

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class OCREngine:
    def __init__(self):
        self.gcv_client = vision.ImageAnnotatorClient()
        self.docai_client = documentai.DocumentProcessorServiceClient()
        self.cache = {}
        self.executor = ThreadPoolExecutor(max_workers=settings.MAX_WORKERS)

    async def process_documents(self, documents: List[Dict[str, any]]) -> Dict[str, Dict]:
        results = {}
        total_documents = len(documents)
        
        async def process_batch(batch):
            batch_results = await asyncio.gather(*[self._process_document(doc) for doc in batch])
            return {doc['filename']: result for doc, result in zip(batch, batch_results)}

        batches = [documents[i:i+settings.BATCH_SIZE] for i in range(0, len(documents), settings.BATCH_SIZE)]
        
        for index, batch in enumerate(batches, 1):
            batch_results = await process_batch(batch)
            results.update(batch_results)
            
            processed_count = min(index * settings.BATCH_SIZE, total_documents)
            status = await self.update_processing_status(total_documents, processed_count)
            logger.info(f"Processing status: {status.dict()}")

        return results

    async def _process_document(self, document: Dict[str, any]) -> Dict:
        try:
            cache_key = hash(document['content'])
            if cache_key in self.cache:
                return self.cache[cache_key]

            if document['is_multipage']:
                ocr_result = await self._process_multipage(document)
            else:
                ocr_result = await self._process_single_page(document)

            extracted_data = await self._extract_structured_data(ocr_result)
            self.cache[cache_key] = extracted_data
            return extracted_data
        except Exception as e:
            logger.error(f"Error processing {document['filename']}: {str(e)}")
            return {"error": str(e)}

    async def _process_multipage(self, document: Dict[str, any]) -> Dict:
        results = []
        for page in document['pages']:
            page_result = await self._process_single_page({'content': page['content'], 'filename': page['filename']})
            results.append(page_result)

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
            ocr_result = await self._process_with_gcv(image_name, preprocessed_image)
            layout_result = await self._analyze_layout(preprocessed_image)
            ocr_result.update(layout_result)
            return ocr_result
        except Exception as e:
            logger.error(f"Error in single page processing for {image_name}: {str(e)}")
            return {"error": str(e)}

    async def _preprocess_image(self, image_bytes: bytes) -> bytes:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self.executor, self._preprocess_image_sync, image_bytes)

    def _preprocess_image_sync(self, image_bytes: bytes) -> bytes:
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
            return {"error": f"GCV API error: {str(e)}"}

    async def _analyze_layout(self, image_bytes: bytes) -> Dict:
        image = vision.Image(content=image_bytes)
        try:
            response = await asyncio.to_thread(self.gcv_client.document_text_detection, image)
            return self._parse_layout(response)
        except Exception as e:
            logger.error(f"Layout analysis error: {str(e)}")
            return {"error": f"Layout analysis error: {str(e)}"}

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
        for row in block.paragraphs:
            table_row = []
            for cell in row.words:
                cell_text = ''.join([symbol.text for symbol in cell.symbols])
                table_row.append(cell_text)
            table.append(table_row)
        return table

    def _extract_key_value_pair(self, block) -> Dict[str, str]:
        text = ''.join([''.join([symbol.text for symbol in word.symbols]) for word in block.words])
        if ':' in text:
            key, value = text.split(':', 1)
            return {key.strip(): value.strip()}
        return None

    async def _extract_structured_data(self, ocr_result: Dict) -> Dict:
        try:
            # Use Document AI to extract structured data
            document = documentai.Document(content=ocr_result['content'], mime_type='application/pdf')
            request = documentai.ProcessRequest(name=settings.DOCAI_PROCESSOR_NAME, document=document)
            response = await asyncio.to_thread(self.docai_client.process_document, request)
            
            # Parse the structured data
            invoice = self._parse_docai_response(response.document)
            return invoice.dict()
        except Exception as e:
            logger.error(f"Error extracting structured data: {str(e)}")
            return {"error": f"Structured data extraction error: {str(e)}"}

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

ocr_engine = OCREngine()
