"""Heuristic banana segmentation on plain, light backgrounds; not a classifier."""
from dataclasses import dataclass

import cv2
import numpy as np
from PIL import Image, ImageOps


@dataclass
class Analysis:
    mask: np.ndarray
    features: dict
    warnings: list[str]


def analyze(image: Image.Image) -> Analysis:
    rgb = np.asarray(ImageOps.exif_transpose(image).convert('RGB'))
    height, width = rgb.shape[:2]
    if min(height, width) < 32:
        raise ValueError('Image must be at least 32 pixels in each dimension')
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    h, s, v = cv2.split(hsv)
    border = np.zeros((height, width), bool)
    border[:3] = border[-3:] = True
    border[:, :3] = border[:, -3:] = True
    warnings = []
    if np.median(s[border]) > 65 or np.median(v[border]) < 100:
        warnings.append('background_outside_plain_light_assumption')
    # Banana-colored seeds exclude blue/purple background color casts.
    candidate = ((h <= 95) & (s > 55) & (v > 30)) | (v < 75)
    candidate[border] = False
    count, components, stats, _ = cv2.connectedComponentsWithStats(candidate.astype('uint8'))
    if count < 2 or stats[1:, cv2.CC_STAT_AREA].max() < 0.005 * height * width:
        return Analysis(np.zeros((height, width), bool), {}, ['no_foreground_found'] + warnings)
    largest = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    seed = (components == largest).astype('uint8')
    seed = cv2.morphologyEx(seed, cv2.MORPH_CLOSE, np.ones((5, 5), 'uint8'))
    # Shadows can be connected to the fruit in the initial component. Keep the
    # high-saturation/dark core as the confident GrabCut foreground seed so the
    # shadow remains probable background instead of becoming part of the mask.
    saturation_cutoff = max(90.0, float(np.percentile(s[seed.astype(bool)], 60)))
    core = seed.astype(bool) & ((s >= saturation_cutoff) | (v < 45))
    sure = cv2.erode(core.astype('uint8'), np.ones((3, 3), 'uint8')).astype(bool)
    if not sure.any():
        return Analysis(np.zeros((height, width), bool), {}, ['insufficient_foreground_seed'] + warnings)
    labels = np.full((height, width), cv2.GC_PR_BGD, 'uint8')
    labels[core] = cv2.GC_PR_FGD
    labels[sure] = cv2.GC_FGD
    labels[border] = cv2.GC_BGD
    cv2.setRNGSeed(42)
    cv2.grabCut(rgb, labels, None, np.zeros((1, 65)), np.zeros((1, 65)), 5, cv2.GC_INIT_WITH_MASK)
    mask = (labels == cv2.GC_FGD) | (labels == cv2.GC_PR_FGD)
    count, components, stats, _ = cv2.connectedComponentsWithStats(mask.astype('uint8'))
    mask = components == (1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA])))
    fraction = float(mask.mean())
    if fraction < 0.03 or fraction > 0.75:
        warnings.append('unusual_foreground_area')
    if mask[3, :].any() or mask[-4, :].any() or mask[:, 3].any() or mask[:, -4].any():
        warnings.append('foreground_near_image_edge')
    # Exclusive HSV bins, intentionally called "like": thresholds are uncalibrated.
    black = mask & (v < 55)
    brown = mask & ~black & (h < 30) & (s >= 55) & (v < 170)
    green = mask & ~black & ~brown & (h >= 32) & (h <= 90) & (s >= 55)
    yellow = mask & ~black & ~brown & ~green & (h >= 15) & (h < 38) & (s >= 55)
    other = mask & ~(black | brown | green | yellow)
    features = {f'{name}_like_ratio': float(pixels.sum() / mask.sum())
                for name, pixels in [('green', green), ('yellow', yellow), ('brown', brown), ('black', black)]}
    features.update(other_ratio=float(other.sum() / mask.sum()),
                    foreground_fraction=fraction, foreground_pixels=int(mask.sum()),
                    mean_saturation=float(s[mask].mean() / 255),
                    mean_brightness=float(v[mask].mean() / 255))
    return Analysis(mask, features, warnings)


def overlay(image: Image.Image, mask: np.ndarray) -> Image.Image:
    rgb = np.asarray(ImageOps.exif_transpose(image).convert('RGB')).copy()
    rgb[~mask] = (rgb[~mask].astype(float) * 0.25).astype('uint8')
    contours, _ = cv2.findContours(mask.astype('uint8'), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cv2.drawContours(rgb, contours, -1, (255, 0, 180), 1)
    return Image.fromarray(rgb)


def main():
    import argparse
    import json
    from pathlib import Path

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('image', type=Path)
    parser.add_argument('--output', type=Path, default=Path('reports/single-image'))
    args = parser.parse_args()
    with Image.open(args.image) as source:
        image = ImageOps.exif_transpose(source).convert('RGB')
    result = analyze(image)
    args.output.mkdir(parents=True, exist_ok=True)
    Image.fromarray(result.mask.astype('uint8') * 255).save(args.output / 'mask.png')
    overlay(image, result.mask).save(args.output / 'overlay.png')
    report = dict(features=result.features, warnings=result.warnings,
                  status='review_required' if result.warnings else 'unvalidated',
                  ripeness_stage=None, days_remaining=None,
                  method='plain-background GrabCut and exclusive HSV bins v1')
    (args.output / 'analysis.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
