# FruitSavor

FruitSavor is a phone-friendly fruit tracking app, starting with bananas. Take photos, record ripeness observations, and follow your fruit over time. The goal is to estimate when fruit is best to eat and how many usable days remain.

Currently a mobile web prototype with photo history, personal check-ins, and exploratory image analysis. Trained ripeness and shelf-life predictions are not available yet.

## Workflow

1. Add a banana and its storage details.
2. Take or upload a photo.
3. Record a check-in and revisit its photo history as it ripens.
4. Export records or create a full backup.

## Tech stack

| Layer | Technology |
| --- | --- |
| Frontend | HTML, CSS, JavaScript |
| Backend | Python 3.11+, FastAPI, Uvicorn |
| Validation | Pydantic |
| Storage | SQLite |
| Image processing | OpenCV, NumPy, Pillow |
| Testing | unittest, HTTPX |
