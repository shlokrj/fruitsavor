"""Measure mask overlap against frozen reviewed references, with provenance."""
from collections import Counter
import hashlib
import io
import inspect

import cv2
import numpy as np
from PIL import Image

from fruitsavor import vision


def overlap(prediction, reference):
    if prediction.shape != reference.shape or prediction.ndim != 2:
        raise ValueError('Masks must have the same two-dimensional shape')
    prediction, reference = prediction.astype(bool), reference.astype(bool)
    if not reference.any():
        raise ValueError('Reference foreground cannot be empty')
    intersection = int((prediction & reference).sum())
    return dict(iou=intersection / int((prediction | reference).sum()),
                dice=2 * intersection / int(prediction.sum() + reference.sum()),
                foreground_recall=intersection / int(reference.sum()),
                foreground_precision=intersection / int(prediction.sum()) if prediction.any() else 0.0)


def evaluate(project, partition='evaluation', allow_assistant=False):
    if partition not in {'development', 'evaluation'}:
        raise ValueError('Unknown partition')
    items = [item for item in project.items.values() if item['partition'] == partition]
    if not items:
        raise ValueError('No samples in this partition')
    snapshot = [(item, project.latest(item['id'], include_mask=True)) for item in items]
    pending = [item['id'] for item, annotation in snapshot
               if annotation is None or annotation['status'] != 'reviewed'
               or (annotation['origin'] != 'human_manual' and not allow_assistant)]
    report = dict(partition=partition, project_sha256=project.manifest_hash,
                  total_images=len(items), eligible_annotations=len(items)-len(pending),
                  status='awaiting_annotations' if pending else 'complete', missing_ids=pending,
                  scope=project.manifest['scope'], metrics=None, by_source_label=None,
                  reference_origins=dict(Counter(a['origin'] for _, a in snapshot if a)),
                  limitation='Image-level mask agreement on a selected challenge subset. '
                             'Reference origin is declared by the annotator; this is not specimen-independent '
                             'classification or shelf-life validation.')
    if pending:
        return report
    cv2.setNumThreads(1)
    scores = []
    for item, annotation in snapshot:
        with Image.open(io.BytesIO(project.image(item['id']))) as source:
            result = vision.analyze(source)
        with Image.open(io.BytesIO(annotation['mask'])) as im:
            reference = np.asarray(im) > 0
        scores.append(dict(id=item['id'], source_label=item['source_label'],
                           reference_revision=annotation['revision'],
                           reference_sha256=annotation['mask_sha256'],
                           reference_origin=annotation['origin'], warnings=result.warnings,
                           **overlap(result.mask, reference)))
    metric_names = ('iou', 'dice', 'foreground_recall', 'foreground_precision')

    def aggregate(rows):
        return dict(images=len(rows), **{f'mean_{name}': float(np.mean([row[name] for row in rows]))
                                        for name in metric_names})

    report.update(metrics=aggregate(scores),
                  by_source_label={label: aggregate([row for row in scores if row['source_label'] == label])
                                   for label in sorted({row['source_label'] for row in scores})},
                  per_image=scores,
                  analysis_code_sha256=hashlib.sha256(inspect.getsource(vision).encode()).hexdigest(),
                  versions=dict(opencv=cv2.__version__, numpy=np.__version__))
    return report
