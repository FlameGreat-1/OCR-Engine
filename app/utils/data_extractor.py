import re
from typing import Dict, List, Tuple
from datetime import datetime
from decimal import Decimal, InvalidOperation
import logging
from google.cloud import vision
from google.cloud import documentai_v1 as documentai
from app.models import Invoice, Vendor, Address, InvoiceItem
from app.config import settings
import asyncio
from concurrent.futures import ThreadPoolExecutor
import dateparser
from price_parser import Price

logger = logging.getLogger(__name__)

class DataExtractor:
    def __init__(self):
        self.gcv_client = vision.ImageAnnotatorClient()
        self.docai_client = documentai.DocumentProcessorServiceClient()
        self.executor = ThreadPoolExecutor(max_workers=settings.MAX_WORKERS)

    async def extract_data(self, ocr_result: Dict) -> Invoice:
        try:
            if ocr_result.get("is_multipage", False):
                return await self._extract_multipage_data(ocr_result)
            else:
                return await self._extract_single_page_data(ocr_result)
        except Exception as e:
            logger.error(f"Error extracting data: {str(e)}")
            return Invoice(filename=ocr_result.get("filename", ""), error=str(e))

    async def _extract_multipage_data(self, ocr_result: Dict) -> Invoice:
        document = documentai.Document(content=ocr_result['content'], mime_type='application/pdf')
        request = documentai.ProcessRequest(name=settings.DOCAI_PROCESSOR_NAME, document=document)
        response = await asyncio.to_thread(self.docai_client.process_document, request)
        return self._parse_docai_response(response.document, ocr_result.get("filename", ""))

    async def _extract_single_page_data(self, ocr_result: Dict) -> Invoice:
        image = vision.Image(content=ocr_result['content'])
        response = await asyncio.to_thread(self.gcv_client.document_text_detection, image)
        return self._parse_gcv_response(response, ocr_result.get("filename", ""))

    def _parse_docai_response(self, document, filename: str) -> Invoice:
        entities = {e.type_: e.mention_text for e in document.entities}
        
        invoice_number = entities.get('invoice_id', '')
        vendor = self._extract_vendor_from_docai(entities)
        invoice_date = self._parse_date(entities.get('invoice_date', ''))
        grand_total = self._parse_decimal(entities.get('subtotal_amount', '0'))
        taxes = self._parse_decimal(entities.get('total_tax_amount', '0'))
        final_total = self._parse_decimal(entities.get('total_amount', '0'))
        items = self._extract_items_from_docai(document)

        return Invoice(
            filename=filename,
            invoice_number=invoice_number,
            vendor=vendor,
            invoice_date=invoice_date,
            grand_total=grand_total,
            taxes=taxes,
            final_total=final_total,
            items=items,
            pages=len(document.pages)
        )

    def _extract_vendor_from_docai(self, entities: Dict[str, str]) -> Vendor:
        return Vendor(
            name=entities.get('supplier_name', ''),
            address=Address(
                street=entities.get('supplier_address', ''),
                city=entities.get('supplier_city', ''),
                state=entities.get('supplier_state', ''),
                country=entities.get('supplier_country', ''),
                postal_code=entities.get('supplier_zip', '')
            )
        )

    def _extract_items_from_docai(self, document) -> List[InvoiceItem]:
        items = []
        for table in document.pages[0].tables:
            for row in table.body_rows:
                try:
                    item = InvoiceItem(
                        description=row.cells[0].layout.text_anchor.content,
                        quantity=int(row.cells[1].layout.text_anchor.content),
                        unit_price=self._parse_decimal(row.cells[2].layout.text_anchor.content),
                        total=self._parse_decimal(row.cells[3].layout.text_anchor.content)
                    )
                    items.append(item)
                except (ValueError, InvalidOperation) as e:
                    logger.warning(f"Error parsing item from DocAI: {str(e)}")
        return items

    def _parse_gcv_response(self, response, filename: str) -> Invoice:
        document = response.full_text_annotation
        text = document.text
        
        invoice_number = self._extract_invoice_number(text)
        vendor = self._extract_vendor(document)
        invoice_date = self._extract_date(text)
        grand_total, taxes, final_total = self._extract_totals(text)
        items = self._extract_items(document)

        return Invoice(
            filename=filename,
            invoice_number=invoice_number,
            vendor=vendor,
            invoice_date=invoice_date,
            grand_total=grand_total,
            taxes=taxes,
            final_total=final_total,
            items=items,
            pages=1
        )

    def _extract_invoice_number(self, text: str) -> str:
        match = re.search(r'(?i)invoice\s*number?[:\s]*([A-Za-z0-9-]{5,})', text)
        return match.group(1) if match else ""

    def _extract_vendor(self, document) -> Vendor:
        for page in document.pages:
            for block in page.blocks:
                if block.block_type == vision.TextAnnotation.DetectedBreak.BreakType.PARAGRAPH:
                    text = ''.join([word.text for word in block.words])
                    if len(text.split()) > 3:  # Assume the first paragraph with more than 3 words is the vendor info
                        return Vendor(name=text.split('\n')[0], address=self._extract_address(text))
        return Vendor(name="Unknown", address=Address())

    def _extract_address(self, text: str) -> Address:
        lines = text.split('\n')
        if len(lines) < 2:
            return Address()
        return Address(
            street=lines[1],
            city=lines[2].split(',')[0] if len(lines) > 2 else "",
            state=lines[2].split(',')[1].strip() if len(lines) > 2 and ',' in lines[2] else "",
            country="",
            postal_code=re.search(r'\d{5}(?:-\d{4})?', text).group() if re.search(r'\d{5}(?:-\d{4})?', text) else ""
        )

    def _extract_date(self, text: str) -> datetime:
        match = re.search(r'(?i)date[:\s]*(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})', text)
        if match:
            return self._parse_date(match.group(1))
        return datetime.now()

    def _extract_totals(self, text: str) -> Tuple[Decimal, Decimal, Decimal]:
        grand_total_match = re.search(r'(?i)subtotal[:\s]*\$([\d,]+\.\d{2})', text)
        tax_match = re.search(r'(?i)tax[:\s]*\$([\d,]+\.\d{2})', text)
        final_total_match = re.search(r'(?i)total[:\s]*\$([\d,]+\.\d{2})', text)
        
        grand_total = self._parse_decimal(grand_total_match.group(1) if grand_total_match else '0.00')
        taxes = self._parse_decimal(tax_match.group(1) if tax_match else '0.00')
        final_total = self._parse_decimal(final_total_match.group(1) if final_total_match else '0.00')

        return grand_total, taxes, final_total

    def _extract_items(self, document) -> List[InvoiceItem]:
        items = []
        for page in document.pages:
            for table in page.tables:
                for row in table.rows[1:]:  # Skip header row
                    if len(row.cells) >= 4:
                        try:
                            items.append(InvoiceItem(
                                description=row.cells[0].text,
                                quantity=int(row.cells[1].text),
                                unit_price=self._parse_decimal(row.cells[2].text),
                                total=self._parse_decimal(row.cells[3].text)
                            ))
                        except (ValueError, InvalidOperation) as e:
                            logger.warning(f"Error parsing item: {str(e)}")
        return items

    def _parse_date(self, date_string: str) -> datetime:
        try:
            return dateparser.parse(date_string)
        except ValueError:
            logger.warning(f"Could not parse date: {date_string}")
            return datetime.now()

    def _parse_decimal(self, amount_string: str) -> Decimal:
        try:
            price = Price.fromstring(amount_string)
            return Decimal(str(price.amount)) if price.amount else Decimal('0.00')
        except InvalidOperation:
            logger.warning(f"Could not parse decimal: {amount_string}")
            return Decimal('0.00')

data_extractor = DataExtractor()
