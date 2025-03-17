import gradio as gr
import requests
import json
import time
import os
from fastapi import FastAPI
from app.main import app as fastapi_app
from app.config import settings

# Initialize the FastAPI app
app = FastAPI()

# Mount the main FastAPI app
app.mount("/api", fastapi_app)

# Render configuration
RENDER_URL = settings.RENDER_URL
API_KEY = settings.API_KEY

def process_invoices(files):
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
        response = requests.post(upload_url, files=files_dict, headers=headers)
        response.raise_for_status()
        
        task_id = response.json()["task_id"]
        
        # Poll for status
        status_url = f"{RENDER_URL}/api/status/{task_id}"
        start_time = time.time()
        while True:
            try:
                status_response = requests.get(status_url, headers=headers)
                status_response.raise_for_status()
                
                status_data = status_response.json()
                status = status_data["status"]["status"]
                progress = status_data["status"]["progress"]
                message = status_data["status"]["message"]
                
                yield f"Status: {status}, Progress: {progress}%, Message: {message}"
                
                if status == "Completed":
                    break
                elif status == "Failed":
                    return f"Processing failed: {message}"
                
                # Check for timeout (e.g., 10 minutes)
                if time.time() - start_time > 600:
                    return "Processing timed out. Please try again later."
                
                time.sleep(5)  # Wait 5 seconds before checking again
            except requests.RequestException:
                yield "Temporary error occurred. Retrying..."
                time.sleep(10)  # Wait longer before retrying
        
        # Download results
        csv_url = f"{RENDER_URL}/api/download/{task_id}?format=csv"
        excel_url = f"{RENDER_URL}/api/download/{task_id}?format=excel"
        
        csv_response = requests.get(csv_url, headers=headers)
        excel_response = requests.get(excel_url, headers=headers)
        
        csv_response.raise_for_status()
        excel_response.raise_for_status()
        
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
        
        validation_response = requests.get(validation_url, headers=headers)
        anomalies_response = requests.get(anomalies_url, headers=headers)
        
        validation_results = validation_response.json() if validation_response.status_code == 200 else {}
        anomalies = anomalies_response.json() if anomalies_response.status_code == 200 else []
        
        return f"Processing completed. Results saved as {csv_path} and {excel_path}\n\nValidation Results: {json.dumps(validation_results, indent=2)}\n\nAnomalies: {json.dumps(anomalies, indent=2)}"
    except requests.RequestException as e:
        return f"Error during API request: {str(e)}"
    except Exception as e:
        return f"Unexpected error: {str(e)}"

# Define the Gradio interface
iface = gr.Interface(
    fn=process_invoices,
    inputs=gr.File(file_count="multiple", label="Upload Invoice Files (PDF, JPG, PNG, or ZIP)"),
    outputs="text",
    title=settings.PROJECT_NAME,
    description="Upload invoice files to extract and validate information. Results will be provided in CSV and Excel formats.",
    live=True
)

# Combine FastAPI and Gradio
app = gr.mount_gradio_app(app, iface, path="/")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=settings.PORT)
