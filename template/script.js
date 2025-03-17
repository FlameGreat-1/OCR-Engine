// script.js

document.addEventListener('DOMContentLoaded', () => {
    const uploadForm = document.getElementById('upload-form');
    const fileInput = document.getElementById('file-input');
    const uploadButton = document.getElementById('upload-button');
    const resultContent = document.getElementById('result-content');
    const progressBar = document.getElementById('progress-bar');
    const progressText = document.getElementById('progress-text');

    uploadForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        const files = fileInput.files;

        if (files.length === 0) {
            alert('Please select at least one file to upload.');
            return;
        }

        uploadButton.disabled = true;
        resultContent.innerHTML = '';
        progressBar.style.width = '0%';
        progressText.textContent = '0%';

        const formData = new FormData();
        for (let file of files) {
            formData.append('files', file);
        }

        try {
            // Start upload
            const uploadResponse = await fetch('/upload/', {
                method: 'POST',
                body: formData,
                headers: {
                    'X-API-Key': 'rnd_0NsClIKTf41W5ULBwvdVkmSac0uE' 
                }
            });

            if (!uploadResponse.ok) {
                throw new Error(`HTTP error! status: ${uploadResponse.status}`);
            }

            const uploadResult = await uploadResponse.json();
            const taskId = uploadResult.task_id;

            // Poll for status
            let processingComplete = false;
            while (!processingComplete) {
                const statusResponse = await fetch(`/status/${taskId}`, {
                    headers: {
                        'X-API-Key': 'your-api-key-here' // Replace with actual API key
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
            const resultsResponse = await fetch(`/download/${taskId}`, {
                headers: {
                    'X-API-Key': 'your-api-key-here' // Replace with actual API key
                }
            });

            if (resultsResponse.ok) {
                const blob = await resultsResponse.blob();
                const url = window.URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.style.display = 'none';
                a.href = url;
                a.download = 'ocr_results.csv';
                document.body.appendChild(a);
                a.click();
                window.URL.revokeObjectURL(url);
                resultContent.innerHTML = '<p>Processing complete. Results downloaded automatically.</p>';
            } else {
                throw new Error('Failed to download results');
            }

            // Fetch and display validation results
            const validationResponse = await fetch(`/validation/${taskId}`, {
                headers: {
                    'X-API-Key': 'your-api-key-here' // Replace with actual API key
                }
            });
            const validationResults = await validationResponse.json();
            displayValidationResults(validationResults);

            // Fetch and display anomalies
            const anomaliesResponse = await fetch(`/anomalies/${taskId}`, {
                headers: {
                    'X-API-Key': 'your-api-key-here' // Replace with actual API key
                }
            });
            const anomalies = await anomaliesResponse.json();
            displayAnomalies(anomalies);

        } catch (error) {
            console.error('Error:', error);
            resultContent.innerHTML = `<p>Error: ${error.message}</p>`;
        } finally {
            uploadButton.disabled = false;
        }
    });

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
});
