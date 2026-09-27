# FruitSavor

Fruit freshness research, starting with banana ripeness and eventually remaining usable shelf life.

Includes a phone-friendly photo tracking interface, persistent FastAPI backend, fruit and scan history, verified data preparation, and exploratory banana segmentation/color analysis. No trained model or shelf-life predictions yet. Ripeness classes do not establish food safety, and elapsed observation time is not a shelf-life target.

## Data

| Source | Use | Limitation |
| --- | --- | --- |
| [BananaImageBD v2](https://data.mendeley.com/datasets/ptfscwtnyz/2) | Initial classification dataset; 820 original images, four stages | Individual fruit identities need auditing before evaluation |
| [BananaID v1](https://data.mendeley.com/datasets/h6n5srjjyw/1) | Additional classification source; 1,960 original images | No documented remaining-life targets |
| [Banana ripening day 0–7 v2](https://data.mendeley.com/datasets/d5tczj7fs7/2) | Temporal experiments; approximately 30 tracked bananas | Final observation is not an observed spoilage endpoint |
| [Kaggle banana ripeness](https://www.kaggle.com/datasets/shahriar26s/banana-ripeness-classification-dataset) | Alternative candidate | License, provenance and augmentation need direct verification |

The three Mendeley sources list [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Use original images and preserve attribution. Dataset versions and download checksums are recorded in `datasets/catalog.json`; downloaded data and generated artifacts stay local.

BananaImageBD attribution: Ferdaus et al. (2024), Mendeley Data, V2, [doi:10.17632/ptfscwtnyz.2](https://doi.org/10.17632/ptfscwtnyz.2). BananaID: Mutrofin, Fatichah, Yuniarti and Setiawan (2025), V1. Day 0–7: sangolgi, narode and Atre (2026), V2.

## Direction

Audit original images → establish fruit-level evaluation groups → segmentation and color features → ripeness baselines → longitudinal shelf-life collection and regression → camera interface.

Future fruit types will share the data interface while retaining fruit-specific models and label definitions.

## Run

Requires Python 3.11 or newer. Run from the repository root:

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
fruitsavor prepare-bananaimagebd
python -m fruitsavor.review
python -m fruitsavor.explore
python -m unittest discover -s tests -v
```

Preparation downloads only the 3.68 MB original ripeness archive, verifies its publisher SHA-256, decodes each image, and writes `data/processed/bananaimagebd/manifest.jsonl` and `audit.json`. Original archive paths, source labels, image hashes and dimensions are retained. Decoded-pixel duplicates are counted; near duplicates still require review.

Fruit IDs, shelf-life targets and splits remain unassigned. The Python `grouped_split` utility requires audited group IDs and rejects exact duplicates across groups. It creates deterministic approximate 70/15/15 splits; class balance and batch independence need separate review. No evaluation is claimed from image-level splits.

Initial audit: 820 images decode successfully; 819 unique decoded images. One duplicate appears under both Ripe and Semi-ripe, so label conflicts need review before training. No specimen-independent evaluation has been run.

Review exact duplicates and retrieve visually similar pairs:

```sh
python -m fruitsavor.review
```

This writes a derived `curated.jsonl` and `review.json`. Both images in the conflicting-label pair are quarantined, leaving 818 images eligible for exploration. A 64-bit difference-hash scan flags 595 candidate pairs at distance ≤4. These are review cues, not confirmed duplicates or specimen identities; no split assignments are inferred from similarity.

## Visual baseline

`python -m fruitsavor.explore` processes curated images and saves masks, `features.jsonl`, a summary and original/overlay comparisons under `reports/color-baseline/`. A single banana on a plain light background is assumed. Analyze your own image with:

```sh
python -m fruitsavor.vision banana.jpg --output reports/my-banana
```

Features include foreground area, mean brightness/saturation, and exclusive green-like, yellow-like, brown-like, black-like and other pixel proportions. These HSV thresholds are uncalibrated visual heuristics; shadows and lighting affect the measurements. Background and area warnings flag some failures, but absence of a warning does not establish a correct mask. Blank images return no features.

No annotated reference masks or verified specimen groups are available yet, so segmentation accuracy and classifier performance remain unmeasured. The next milestone is annotated mask validation and a defensible grouped evaluation dataset before training.

Current exploratory run: 818 images processed; 106 flagged for review. These counts describe processing and warnings, not predictive accuracy. The development-only shadow-seed refinement is covered by a synthetic connected-shadow test and raised the assistant-reference pilot's mean IoU from 0.873 to 0.916; human-reviewed evaluation remains pending.

## Backend

Start from the repository root after installation:

```sh
fruitsavor-api
```

The API listens at `http://127.0.0.1:8000`; interactive documentation is at `/docs`. Python 3.14 dependency versions used for verification are pinned in `requirements.lock`; reproduce them with `pip install -c requirements.lock -e ".[dev]"`. State and normalized image artifacts are stored together in `data/fruitsavor.sqlite3`, persist across restarts, and stay out of Git. This is a single-user backend.

Open `/` for the mobile web preview: add a named banana, take or choose a photo, record its capture time, and revisit its photo history. Camera selection depends on the phone/browser; a separate photo-library picker is also available. HEIC is not supported yet. Ripeness, best-to-eat timing and days remaining are explicitly unavailable. This is a browser preview, not an installable native app or offline app.

The blue-and-cream interface includes an expandable add-fruit form and a photo preview with removal before saving. Display type uses locally bundled [Fredoka](https://github.com/google/fonts/tree/main/ofl/fredoka); its SIL Open Font License is included beside the font. No external font requests are made.

When a server token is configured, enter it in the interface; it stays only in tab memory and must be entered again after reloading. Images are fetched with the same authorization as records. The application shell is public but fruit records and photos remain protected. For phone testing, serve through an HTTPS endpoint reachable from the phone; the computer’s loopback URL is only accessible on that computer.

| Endpoint | Behavior |
| --- | --- |
| `GET /health`, `GET /capabilities` | Storage health and supported analysis capabilities |
| `POST /analyze` | Upload and save a standalone analysis |
| `POST /fruit`, `GET /fruit` | Create and list tracked fruit |
| `GET`, `PATCH`, `DELETE /fruit/{id}` | Read, edit or delete fruit and its scans |
| `POST /fruit/{id}/scan` | Upload a scan for existing fruit |
| `GET /fruit/{id}/history` | Scan history, newest capture first |
| `GET /scans`, `GET /scans/{id}` | List or retrieve saved analyses |
| `DELETE /scans/{id}` | Delete a scan and its artifacts |
| `GET /scans/{id}/artifacts/{image,mask,overlay}` | Retrieve normalized PNG artifacts |

```sh
curl -F 'file=@banana.jpg' http://127.0.0.1:8000/analyze
curl -H 'Content-Type: application/json' \
  -d '{"name":"Kitchen banana","storage_method":"counter"}' \
  http://127.0.0.1:8000/fruit
```

Uploads accept JPEG, PNG or static WebP, up to 10 MiB and 20 million source pixels. Images are oriented, stripped of metadata and resized to a maximum 512-pixel edge. Scan forms also accept `captured_at` (timezone required), `temperature_c`, and `storage_method`. Lists accept `limit` (1–100) and `offset`. Artifact URLs are included in each scan result.

Analysis returns `unvalidated`, `review_required` or `insufficient_image`. Predictions explicitly return `status: unavailable`; ripeness, freshness, confidence and days remaining are null. A blank image returns no features. Concurrent analysis receives `503` with `Retry-After`; other requests remain available. POST requests create new records, so retries after an uncertain network outcome may create duplicates.

Configuration uses exported environment variables:

- `FRUITSAVOR_DATABASE`: database path; defaults to `data/fruitsavor.sqlite3`.
- `FRUITSAVOR_API_TOKEN`: optional shared bearer token. When set, send `Authorization: Bearer ...` for records, analysis and artifacts. Health and API docs remain public.
- `FRUITSAVOR_ALLOWED_ORIGINS`: comma-separated browser origins, such as `http://localhost:3000`. Default: no cross-origin access.

`fruitsavor-api --host 0.0.0.0` requires a token. Use TLS and appropriate hosting controls before exposing it beyond a trusted local environment. There are no user accounts or per-user isolation. Preserve the data directory; use SQLite's online backup mechanism or stop the server before copying its database. Deleting fruit also deletes its scans and artifacts; scans have no automatic expiry.

## Reference-mask validation

Prepare the fixed annotation subset after running dataset preparation, review and exploration:

```sh
python -m fruitsavor.annotation.cli prepare
python -m fruitsavor.annotation.cli serve
```

Open `http://127.0.0.1:8001` to draw masks with an outline, brush or eraser. Save drafts, then mark checked references reviewed. The workbench shows original images without predicted masks. Each save retains its previous revision and records the annotator, declared origin and source/mask checksums. Annotation origin cannot be changed on later revisions.

The pinned protocol selects 16 development and 16 evaluation images, balanced by source stage and including difficult backgrounds, edges and sizes where available. Evaluation excludes known previously viewed images and their similarity groups; those groups do not establish specimen identity. This is a selected image-level segmentation audit, not a representative classification test set.

```sh
python -m fruitsavor.annotation.cli evaluate
```

Evaluation withholds metrics until every selected reference is reviewed and human-drawn. It reports per-image and mean IoU, Dice, foreground precision and recall, along with reference revisions and the analysis code checksum. `--partition development --allow-assistant` explicitly enables a separately attributed assistant-reference diagnostic. These references are approximate and do not establish independently validated accuracy. Projects live under `data/annotations/`; results remain in `reports/`.
