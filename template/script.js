
const BASE_URL = window.location.origin;
let apiKey = API_KEY;
let currentTaskId = null;

document.addEventListener('DOMContentLoaded', async () => {
    const uploadForm = document.getElementById('upload-form');
    const fileInput = document.getElementById('file-input');
    const uploadButton = document.getElementById('upload-button');
    const cancelButton = document.getElementById('cancel-button');
    const resultContent = document.getElementById('result-content');
    const progressBar = document.getElementById('progress-bar');
    const progressText = document.getElementById('progress-text');
    const errorDisplay = document.getElementById('error-display');
    const apiKeyStatus = document.getElementById('api-key-status');

    // Check API key
    if (!apiKey) {
        apiKeyStatus.style.display = 'block';
        apiKeyStatus.textContent = 'Warning: API key not detected. Some features may be limited.';
    } else {
        apiKeyStatus.style.display = 'none';
    }

    // Define showError function
    function showError(message) {
        errorDisplay.style.display = 'block';
        errorDisplay.textContent = message;
        progressBar.style.width = '0%';
        progressText.textContent = 'Error';
    }

    uploadForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        const files = fileInput.files;

        if (files.length === 0) {
            showError('Please select at least one file to upload.');
            return;
        }

        // Check file types
        for (let file of files) {
            const fileType = file.type;
            if (!['application/pdf', 'image/jpeg', 'image/png', 'application/zip'].includes(fileType)) {
                showError(`Unsupported file type: ${fileType}. Please upload PDF, JPEG, PNG, or ZIP files only.`);
                return;
            }
        }

        uploadButton.disabled = true;
        cancelButton.disabled = false;
        resultContent.innerHTML = '';
        progressBar.style.width = '0%';
        progressText.textContent = '0%';
        errorDisplay.style.display = 'none';

        const formData = new FormData();
        for (let file of files) {
            formData.append('files', file);
        }

        const maxWaitTime = 300000; // 5 minutes in milliseconds
        const startTime = Date.now();

        try {
            // Start upload
            const uploadResponse = await fetch(`${BASE_URL}/upload/`, {
                method: 'POST',
                body: formData,
                headers: {
                    'X-API-Key': apiKey
                }
            });

            if (!uploadResponse.ok) {
                const errorText = await uploadResponse.text();
                throw new Error(`Server error (${uploadResponse.status}): ${errorText}`);
            }

            const uploadResult = await uploadResponse.json();
            currentTaskId = uploadResult.task_id;
            
            resultContent.innerHTML = `<p>Upload successful. Task ID: ${currentTaskId}</p>`;

            // Poll for status
            let processingComplete = false;
            while (!processingComplete) {
                // Check for timeout
                if (Date.now() - startTime > maxWaitTime) {
                    throw new Error('Processing timed out after 5 minutes');
                }

                const statusResponse = await fetch(`${BASE_URL}/status/${currentTaskId}`, {
                    headers: {
                        'X-API-Key': apiKey
                    }
                });

                if (!statusResponse.ok) {
                    const errorText = await statusResponse.text();
                    throw new Error(`Server error (${statusResponse.status}): ${errorText}`);
                }

                const statusResult = await statusResponse.json();
                const status = statusResult.status;

                // Update progress bar and text
                progressBar.style.width = `${status.progress}%`;
                progressText.textContent = `${status.progress}% - ${status.message}`;

                if (status.status === 'Completed') {
                    processingComplete = true;
                } else if (status.status === 'Failed') {
                    throw new Error(`Processing failed: ${status.message}`);
                } else {
                    await new Promise(resolve => setTimeout(resolve, 2000)); // Poll every 2 seconds
                }
            }

            // Fetch results
            resultContent.innerHTML += '<p>Processing complete. Downloading results...</p>';
            
            try {
                await downloadResults('csv');
                resultContent.innerHTML += '<p>CSV results downloaded.</p>';
            } catch (error) {
                resultContent.innerHTML += `<p>Error downloading CSV: ${error.message}</p>`;
            }
            
            try {
                await downloadResults('excel');
                resultContent.innerHTML += '<p>Excel results downloaded.</p>';
            } catch (error) {
                resultContent.innerHTML += `<p>Error downloading Excel: ${error.message}</p>`;
            }

            // Fetch and display validation results
            try {
                const validationResults = await fetchValidationResults();
                displayValidationResults(validationResults);
            } catch (error) {
                resultContent.innerHTML += `<p>Error fetching validation results: ${error.message}</p>`;
            }

            // Fetch and display anomalies
            try {
                const anomalies = await fetchAnomalies();
                displayAnomalies(anomalies);
            } catch (error) {
                resultContent.innerHTML += `<p>Error fetching anomalies: ${error.message}</p>`;
            }

            resultContent.innerHTML += '<p>All processing complete.</p>';

        } catch (error) {
            console.error('Error:', error);
            showError(`Error: ${error.message}`);
        } finally {
            uploadButton.disabled = false;
            cancelButton.disabled = true;
            currentTaskId = null;
        }
    });

    cancelButton.addEventListener('click', async () => {
        if (currentTaskId) {
            try {
                const response = await fetch(`${BASE_URL}/cancel/${currentTaskId}`, {
                    method: 'POST',
                    headers: {
                        'X-API-Key': apiKey
                    }
                });
                
                if (!response.ok) {
                    const errorText = await response.text();
                    throw new Error(`Server error (${response.status}): ${errorText}`);
                }
                
                const result = await response.json();
                resultContent.innerHTML = `<p>${result.status}</p>`;
                progressBar.style.width = '0%';
                progressText.textContent = 'Cancelled';
                uploadButton.disabled = false;
                cancelButton.disabled = true;
                currentTaskId = null;
            } catch (error) {
                console.error('Error cancelling task:', error);
                showError(`Error cancelling task: ${error.message}`);
            }
        }
    });

    async function downloadResults(format) {
        const resultsResponse = await fetch(`${BASE_URL}/download/${currentTaskId}?format=${format}`, {
            headers: {
                'X-API-Key': apiKey
            }
        });

        if (!resultsResponse.ok) {
            const errorText = await resultsResponse.text();
            throw new Error(`Failed to download ${format} results: ${errorText}`);
        }

        const blob = await resultsResponse.blob();
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.style.display = 'none';
        a.href = url;
        a.download = format === 'excel' ? 'ocr_results.xlsx' : 'ocr_results.csv';
        document.body.appendChild(a);
        a.click();
        window.URL.revokeObjectURL(url);
        document.body.removeChild(a);
    }

    async function fetchValidationResults() {
        const validationResponse = await fetch(`${BASE_URL}/validation/${currentTaskId}`, {
            headers: {
                'X-API-Key': apiKey
            }
        });
        
        if (!validationResponse.ok) {
            const errorText = await validationResponse.text();
            throw new Error(`Failed to fetch validation results: ${errorText}`);
        }
        
        return await validationResponse.json();
    }

    async function fetchAnomalies() {
        const anomaliesResponse = await fetch(`${BASE_URL}/anomalies/${currentTaskId}`, {
            headers: {
                'X-API-Key': apiKey
            }
        });
        
        if (!anomaliesResponse.ok) {
            const errorText = await anomaliesResponse.text();
            throw new Error(`Failed to fetch anomalies: ${errorText}`);
        }
        
        return await anomaliesResponse.json();
    }

    function displayValidationResults(results) {
        let validationHtml = '<h3>Validation Results:</h3>';
        
        if (Object.keys(results).length === 0) {
            validationHtml += '<p>No validation issues found.</p>';
        } else {
            validationHtml += '<ul>';
            for (const [invoiceNumber, warnings] of Object.entries(results)) {
                if (warnings && warnings.length > 0) {
                    validationHtml += `<li>Invoice ${invoiceNumber}:<ul>`;
                    for (const warning of warnings) {
                        validationHtml += `<li>${warning}</li>`;
                    }
                    validationHtml += '</ul></li>';
                }
            }
            validationHtml += '</ul>';
        }
        
        resultContent.innerHTML += validationHtml;
    }

    function displayAnomalies(anomalies) {
        let anomaliesHtml = '<h3>Detected Anomalies:</h3>';
        
        if (!anomalies || anomalies.length === 0) {
            anomaliesHtml += '<p>No anomalies detected.</p>';
        } else {
            anomaliesHtml += '<ul>';
            for (const anomaly of anomalies) {
                anomaliesHtml += `<li>Invoice ${anomaly.invoice_number}: `;
                if (anomaly.flags && anomaly.flags.length > 0) {
                    anomaliesHtml += `<ul>`;
                    for (const flag of anomaly.flags) {
                        anomaliesHtml += `<li>${flag}</li>`;
                    }
                    anomaliesHtml += `</ul>`;
                } else {
                    anomaliesHtml += 'No specific flags';
                }
                anomaliesHtml += `</li>`;
            }
            anomaliesHtml += '</ul>';
        }
        
        resultContent.innerHTML += anomaliesHtml;
    }
});
