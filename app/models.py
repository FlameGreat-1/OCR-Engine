from pydantic import BaseModel, Field, validator, constr
from typing import List, Optional
from datetime import date, datetime
from decimal import Decimal
import re

class Address(BaseModel):
    street: Optional[str] = ""
    city: Optional[str] = ""
    state: Optional[str] = ""
    country: Optional[str] = ""
    postal_code: Optional[str] = ""

class Vendor(BaseModel):
    name: Optional[str] = ""  # Changed from "Unknown Vendor" to empty string
    address: Address

class InvoiceItem(BaseModel):
    description: Optional[str] = ""  # Changed from "Unspecified Item" to empty string
    quantity: Optional[int] = None  # Changed from default=1 to None
    unit_price: Optional[Decimal] = None  # Changed from default=Decimal('0') to None
    total: Optional[Decimal] = None  # Changed from default=Decimal('0') to None

    @validator('total')
    def validate_item_total(cls, v, values):
        if v is not None and 'quantity' in values and values['quantity'] is not None and 'unit_price' in values and values['unit_price'] is not None:
            expected_total = values['quantity'] * values['unit_price']
            if abs(v - expected_total) > Decimal('0.01'):
                return v
        return v

class Invoice(BaseModel):
    filename: constr(min_length=1)
    invoice_number: Optional[str] = None
    vendor: Vendor
    invoice_date: Optional[date] = None
    grand_total: Optional[Decimal] = None  # Changed from default=Decimal('0') to None
    taxes: Optional[Decimal] = None  # Changed from default=Decimal('0') to None
    final_total: Optional[Decimal] = None  # Changed from default=Decimal('0') to None
    items: List[InvoiceItem] = []
    pages: int = Field(default=1, ge=1)

    @validator('final_total')
    def validate_final_total(cls, v, values):
        if v is not None and 'grand_total' in values and values['grand_total'] is not None and 'taxes' in values and values['taxes'] is not None:
            expected_total = values['grand_total'] + values['taxes']
            if abs(v - expected_total) > Decimal('0.01'):
                return v
        return v

    @validator('invoice_date')
    def validate_invoice_date(cls, v):
        if v and v > date.today():
            return date.today()
        return v

class ProcessingResult(BaseModel):
    success: bool
    message: str
    invoices: List[Invoice] = []
    errors: List[str] = []

class FileUpload(BaseModel):
    filename: constr(min_length=1)
    content_type: str
    file_size: int

    @validator('content_type')
    def validate_content_type(cls, v):
        allowed_types = {'application/pdf', 'image/jpeg', 'image/png', 'application/zip'}
        if v not in allowed_types:
            raise ValueError(f"Unsupported file type: {v}")
        return v

    @validator('file_size')
    def validate_file_size(cls, v):
        max_size = 100 * 1024 * 1024  # 100MB
        if v > max_size:
            raise ValueError(f"File size exceeds maximum allowed size of 100MB")
        return v

class ExportFormat(BaseModel):
    format: str = Field(..., regex='^(csv|excel)$')

class ProcessingStatus(BaseModel):
    status: str
    progress: float = Field(ge=0, le=100)
    message: Optional[str]
