# FruitSavor

Fruit freshness research, starting with banana ripeness and eventually remaining usable shelf life.

Includes verified data preparation, duplicate review, and an exploratory banana segmentation and color pipeline. No trained model or shelf-life predictions yet. Ripeness classes do not establish food safety, and elapsed observation time is not a shelf-life target.

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
pip install -e .
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

Current exploratory run: 818 images processed; 106 flagged for review. These counts describe processing and warnings, not predictive accuracy.
