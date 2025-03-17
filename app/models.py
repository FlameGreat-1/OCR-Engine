from pydantic import BaseModel, Field, validator, constr
from typing import List, Optional
from datetime import date
from decimal import Decimal
import re

class Address(BaseModel):
    street: constr(min_length=1)
    city: constr(min_length=1)
    state: Optional[str]
    country: constr(min_length=1)
    postal_code: constr(min_length=1)

class Vendor(BaseModel):
    name: constr(min_length=1)
    address: Address

class InvoiceItem(BaseModel):
    description: constr(min_length=1)
    quantity: int = Field(gt=0)
    unit_price: Decimal = Field(ge=0)
    total: Decimal = Field(ge=0)

    @validator('total')
    def validate_item_total(cls, v, values):
        if 'quantity' in values and 'unit_price' in values:
            expected_total = values['quantity'] * values['unit_price']
            if abs(v - expected_total) > Decimal('0.01'):
                raise ValueError(f"Item total {v} does not match quantity * unit price")
        return v

class Invoice(BaseModel):
    filename: constr(min_length=1)
    invoice_number: str = Field(..., regex=r'^[A-Za-z0-9-]{5,}$')
    vendor: Vendor
    invoice_date: date
    grand_total: Decimal = Field(ge=0)
    taxes: Decimal = Field(ge=0)
    final_total: Decimal = Field(ge=0)
    items: List[InvoiceItem] = Field(min_items=1)
    pages: int = Field(ge=1)

    @validator('final_total')
    def validate_final_total(cls, v, values):
        if 'grand_total' in values and 'taxes' in values:
            expected_total = values['grand_total'] + values['taxes']
            if abs(v - expected_total) > Decimal('0.01'):
                raise ValueError(f"Final total {v} does not match grand total {values['grand_total']} plus taxes {values['taxes']}")
        return v

    @validator('invoice_date')
    def validate_invoice_date(cls, v):
        if v > date.today():
            raise ValueError("Invoice date cannot be in the future")
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

    
