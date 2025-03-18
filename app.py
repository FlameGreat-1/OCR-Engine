import gradio as gr
import requests
import json
import time
import os
from fastapi import FastAPI, HTTPException
from app.main import app as fastapi_app
from app.config import settings
from requests.exceptions import RequestException, Timeout
from ratelimit import limits, sleep_and_retry

# Initialize the FastAPI app
app = FastAPI()

# Mount the main FastAPI app
app.mount("/api", fastapi_app)

# Render configuration
RENDER_URL = settings.RENDER_URL
API_KEY = settings.API_KEY

# Rate limiting: 100 requests per minute
@sleep_and_retry
@limits(calls=100, period=60)
def rate_limited_request(*args, **kwargs):
    return requests.request(*args, **kwargs)

def process_invoices(files, progress=gr.Progress()):
    try:
        # Validate file types
        allowed_types = ['application/pdf', 'image/jpeg', 'image/png', 'application/zip']
        for file in files:
            if file.type not in allowed_types:
                return f"Error: Unsupported file type {file.type}. Please upload PDF, JPG, PNG, or ZIP files only."

        # Upload files
        upload_url = f"{RENDER_URL}/api/upload/"
        files_dict = [("files", (file.name, file.read(), file.type)) for file in files]
        headers = {"X-API-Key": API_KEY}
        
        try:
            response = rate_limited_request("POST", upload_url, files=files_dict, headers=headers, timeout=60)
            response.raise_for_status()
        except Timeout:
            return "Error: File upload timed out. Please try again or upload smaller files."
        except RequestException as e:
            return f"Error during file upload: {str(e)}"
        
        task_id = response.json()["task_id"]
        
        # Poll for status
        status_url = f"{RENDER_URL}/api/status/{task_id}"
        start_time = time.time()
        while True:
            try:
                status_response = rate_limited_request("GET", status_url, headers=headers, timeout=10)
                status_response.raise_for_status()
                
                status_data = status_response.json()
                status = status_data["status"]["status"]
                progress_value = status_data["status"]["progress"]
                message = status_data["status"]["message"]
                
                progress(progress_value / 100, f"Status: {status}, Message: {message}")
                
                if status == "Completed":
                    break
                elif status == "Failed":
                    return f"Processing failed: {message}"
                
                # Check for timeout (e.g., 10 minutes)
                if time.time() - start_time > 600:
                    return "Processing timed out. Please try again later."
                
                time.sleep(5)  # Wait 5 seconds before checking again
            except RequestException as e:
                yield f"Temporary error occurred: {str(e)}. Retrying..."
                time.sleep(10)  # Wait longer before retrying
        
        # Download results
        csv_url = f"{RENDER_URL}/api/download/{task_id}?format=csv"
        excel_url = f"{RENDER_URL}/api/download/{task_id}?format=excel"
        
        try:
            csv_response = rate_limited_request("GET", csv_url, headers=headers, timeout=30)
            excel_response = rate_limited_request("GET", excel_url, headers=headers, timeout=30)
            
            csv_response.raise_for_status()
            excel_response.raise_for_status()
        except RequestException as e:
            return f"Error downloading results: {str(e)}"
        
        # Save downloaded files
        output_dir = "output"
        os.makedirs(output_dir, exist_ok=True)
        csv_path = os.path.join(output_dir, f"invoices_{task_id}.csv")
        excel_path = os.path.join(output_dir, f"invoices_{task_id}.xlsx")
        
        with open(csv_path, "wb") as f:
            f.write(csv_response.content)
        with open(excel_path, "wb") as f:
            f.write(excel_response.content)
        
        # Get validation results and anomalies
        validation_url = f"{RENDER_URL}/api/validation/{task_id}"
        anomalies_url = f"{RENDER_URL}/api/anomalies/{task_id}"
        
        try:
            validation_response = rate_limited_request("GET", validation_url, headers=headers, timeout=10)
            anomalies_response = rate_limited_request("GET", anomalies_url, headers=headers, timeout=10)
            
            validation_results = validation_response.json() if validation_response.status_code == 200 else {}
            anomalies = anomalies_response.json() if anomalies_response.status_code == 200 else []
        except RequestException as e:
            return f"Error retrieving validation results and anomalies: {str(e)}"
        
        return f"Processing completed. Results saved as {csv_path} and {excel_path}\n\nValidation Results: {json.dumps(validation_results, indent=2)}\n\nAnomalies: {json.dumps(anomalies, indent=2)}"
    except Exception as e:
        return f"Unexpected error: {str(e)}"

def cancel_task(task_id):
    try:
        cancel_url = f"{RENDER_URL}/api/cancel/{task_id}"
        headers = {"X-API-Key": API_KEY}
        response = rate_limited_request("POST", cancel_url, headers=headers, timeout=10)
        response.raise_for_status()
        return "Task cancelled successfully"
    except RequestException as e:
        return f"Error cancelling task: {str(e)}"

# Define the Gradio interface
with gr.Blocks() as iface:
    gr.Markdown(f"# {settings.PROJECT_NAME}")
    gr.Markdown("Upload invoice files to extract and validate information. Results will be provided in CSV and Excel formats.")
    
    with gr.Row():
        file_input = gr.File(file_count="multiple", label="Upload Invoice Files (PDF, JPG, PNG, or ZIP)")
        process_button = gr.Button("Process Invoices")
    
    output_text = gr.Textbox(label="Processing Output")
    cancel_button = gr.Button("Cancel Processing")

    process_button.click(
        process_invoices,
        inputs=[file_input],
        outputs=[output_text]
    )

    cancel_button.click(
        cancel_task,
        inputs=[],
        outputs=[output_text]
    )

# Combine FastAPI and Gradio
app = gr.mount_gradio_app(app, iface, path="/")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=settings.PORT)
