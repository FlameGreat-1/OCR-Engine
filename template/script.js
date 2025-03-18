// script.js

const BASE_URL = "https://ocr-engine-04dt.onrender.com";
let apiKey;

async function getApiKey() {
    try {
        const response = await fetch(`${BASE_URL}/api-key`);
        if (response.ok) {
            const data = await response.json();
            return data.api_key;
        } else {
            throw new Error('Failed to get API key');
        }
    } catch (error) {
        console.error('Error fetching API key:', error);
        return null;
    }
}

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

    // Fetch API key when the page loads
    apiKey = await getApiKey();
    if (!apiKey) {
        apiKeyStatus.style.display = 'block';
        apiKeyStatus.textContent = 'Warning: API key not detected. Some features may be limited.';
    } else {
        apiKeyStatus.style.display = 'none';
    }

    let currentTaskId = null;

    uploadForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        const files = fileInput.files;

        if (files.length === 0) {
            showError('Please select at least one file to upload.');
            return;
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
                throw new Error(`HTTP error! status: ${uploadResponse.status}`);
            }

            const uploadResult = await uploadResponse.json();
            currentTaskId = uploadResult.task_id;

            // Poll for status
            let processingComplete = false;
            while (!processingComplete) {
                const statusResponse = await fetch(`${BASE_URL}/status/${currentTaskId}`, {
                    headers: {
                        'X-API-Key': apiKey
                    }
                });
                const statusResult = await statusResponse.json();

                progressBar.style.width = `${statusResult.status.progress}%`;
                progressText.textContent = `${statusResult.status.progress}%`;

                if (statusResult.status.status === 'Completed') {
                    processingComplete = true;
                } else if (statusResult.status.status === 'Failed') {
                    throw new Error('Processing failed');
                } else {
                    await new Promise(resolve => setTimeout(resolve, 2000)); // Poll every 2 seconds
                }
            }

            // Fetch results
            await downloadResults('csv');
            await downloadResults('excel');

            // Fetch and display validation results
            const validationResults = await fetchValidationResults();
            displayValidationResults(validationResults);

            // Fetch and display anomalies
            const anomalies = await fetchAnomalies();
            displayAnomalies(anomalies);

            resultContent.innerHTML += '<p>Processing complete. Results downloaded automatically.</p>';

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
                const result = await response.json();
                resultContent.innerHTML = `<p>${result.status}</p>`;
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

        if (resultsResponse.ok) {
            const blob = await resultsResponse.blob();
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.style.display = 'none';
            a.href = url;
            a.download = `ocr_results.${format}`;
            document.body.appendChild(a);
            a.click();
            window.URL.revokeObjectURL(url);
        } else {
            throw new Error(`Failed to download ${format} results`);
        }
    }

    async function fetchValidationResults() {
        const validationResponse = await fetch(`${BASE_URL}/validation/${currentTaskId}`, {
            headers: {
                'X-API-Key': apiKey
            }
        });
        return await validationResponse.json();
    }

    async function fetchAnomalies() {
        const anomaliesResponse = await fetch(`${BASE_URL}/anomalies/${currentTaskId}`, {
            headers: {
                'X-API-Key': apiKey
            }
        });
        return await anomaliesResponse.json();
    }

    function displayValidationResults(results) {
        let validationHtml = '<h3>Validation Results:</h3><ul>';
        for (const [key, value] of Object.entries(results)) {
            validationHtml += `<li>${key}: ${value}</li>`;
        }
        validationHtml += '</ul>';
        resultContent.innerHTML += validationHtml;
    }

    function displayAnomalies(anomalies) {
        let anomaliesHtml = '<h3>Detected Anomalies:</h3><ul>';
        for (const anomaly of anomalies) {
            anomaliesHtml += `<li>${anomaly}</li>`;
        }
        anomaliesHtml += '</ul>';
        resultContent.innerHTML += anomaliesHtml;
    }

    function showError(message) {
        errorDisplay.style.display = 'block';
        errorDisplay.textContent = message;
    }
});
