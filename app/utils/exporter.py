import pandas as pd
import io
import logging
from typing import List
from app.models import Invoice
from app.config import settings
import asyncio
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)

class InvoiceExporter:
    def __init__(self):
        self.columns = [
            "Filename", "Invoice Number", "Vendor Name", "Address", 
            "Invoice Date", "Grand Total", "Taxes", "Final Total", 
            "Quantity", "Unit Price", "Total", "Pages"
        ]
        self.executor = ThreadPoolExecutor(max_workers=settings.MAX_WORKERS)

    async def export_invoices(self, invoices: List[Invoice], format: str) -> io.BytesIO:
        try:
            df = await self._create_dataframe(invoices)
            if format.lower() == 'csv':
                return await self._export_to_csv(df)
            elif format.lower() == 'excel':
                return await self._export_to_excel(df)
            else:
                raise ValueError(f"Unsupported export format: {format}")
        except Exception as e:
            logger.error(f"Error during invoice export: {str(e)}")
            raise

    async def _create_dataframe(self, invoices: List[Invoice]) -> pd.DataFrame:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self.executor, self._create_dataframe_sync, invoices)

    def _create_dataframe_sync(self, invoices: List[Invoice]) -> pd.DataFrame:
        data = []
        for invoice in invoices:
            # Combine address fields into a single address
            address_parts = [
                invoice.vendor.address.street,
                invoice.vendor.address.city,
                invoice.vendor.address.state,
                invoice.vendor.address.postal_code,
                invoice.vendor.address.country
            ]
            address = ", ".join([part for part in address_parts if part])
            
            # Calculate aggregated values for line items
            total_quantity = 0
            total_amount = 0
            avg_unit_price = 0
            
            if invoice.items:
                for item in invoice.items:
                    if item.quantity is not None:
                        total_quantity += item.quantity
                    if item.total is not None:
                        total_amount += item.total
                
                # Calculate average unit price if we have quantities
                if total_quantity > 0:
                    avg_unit_price = total_amount / total_quantity
            
            row = {
                "Filename": invoice.filename,
                "Invoice Number": invoice.invoice_number,
                "Vendor Name": invoice.vendor.name,
                "Address": address,
                "Invoice Date": invoice.invoice_date,
                "Grand Total": invoice.grand_total,
                "Taxes": invoice.taxes,
                "Final Total": invoice.final_total,
                "Quantity": total_quantity,
                "Unit Price": avg_unit_price,
                "Total": total_amount,
                "Pages": invoice.pages
            }
            data.append(row)

        df = pd.DataFrame(data, columns=self.columns)
        return df

    async def _export_to_csv(self, df: pd.DataFrame) -> io.BytesIO:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self.executor, self._export_to_csv_sync, df)

    def _export_to_csv_sync(self, df: pd.DataFrame) -> io.BytesIO:
        output = io.BytesIO()
        df.to_csv(output, index=False, float_format='%.2f')
        output.seek(0)
        return output

    async def _export_to_excel(self, df: pd.DataFrame) -> io.BytesIO:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self.executor, self._export_to_excel_sync, df)

    def _export_to_excel_sync(self, df: pd.DataFrame) -> io.BytesIO:
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, sheet_name='Invoices', index=False)
            
            workbook = writer.book
            sheet = workbook['Invoices']
            for column in sheet.columns:
                max_length = 0
                column_letter = column[0].column_letter
                for cell in column:
                    try:
                        if len(str(cell.value)) > max_length:
                            max_length = len(cell.value)
                    except:
                        pass
                adjusted_width = (max_length + 2)
                sheet.column_dimensions[column_letter].width = adjusted_width

        output.seek(0)
        return output

async def export_invoices(invoices: List[Invoice], format: str) -> io.BytesIO:
    exporter = InvoiceExporter()
    return await exporter.export_invoices(invoices, format)
