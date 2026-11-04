"""ORB reference matching and capture-to-reference homographies."""

from dataclasses import dataclass

import cv2
import numpy as np

from ..imaging import gray
from .model import Template


@dataclass
class Alignment:
    template: Template
    image: np.ndarray
    homography: np.ndarray
    inliers: int


def align_to_reference(
    image: np.ndarray, reference: np.ndarray, min_inliers: int = 25
) -> tuple[np.ndarray, np.ndarray, int] | None:
    orb = cv2.ORB_create(nfeatures=1500)
    captured_points, captured_desc = orb.detectAndCompute(gray(image), None)
    reference_points, reference_desc = orb.detectAndCompute(gray(reference), None)
    if captured_desc is None or reference_desc is None or len(reference_desc) < 2:
        return None
    pairs = cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(captured_desc, reference_desc, k=2)
    good = [
        pair[0] for pair in pairs if len(pair) == 2 and pair[0].distance < 0.75 * pair[1].distance
    ]
    if len(good) < max(4, min_inliers):
        return None
    src = np.float32([captured_points[m.queryIdx].pt for m in good])
    dst = np.float32([reference_points[m.trainIdx].pt for m in good])
    homography, mask = cv2.findHomography(src, dst, cv2.RANSAC, 5.0)
    if homography is None or mask is None or not np.isfinite(homography).all():
        return None
    inliers = int(mask.sum())
    if inliers < min_inliers:
        return None
    warped = cv2.warpPerspective(image, homography, (reference.shape[1], reference.shape[0]))
    return warped, homography, inliers


def match_reference(templates: list[Template], image: np.ndarray) -> Alignment | None:
    best = None
    for template in templates:
        path = template.reference_path
        if path is None:
            continue
        reference = cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_COLOR)
        if reference is None:
            continue
        result = align_to_reference(
            image, reference, template.data.get("match", {}).get("reference_min_inliers", 25)
        )
        if result is not None:
            warped, homography, inliers = result
            if best is None or (inliers, template.user) > (best.inliers, best.template.user):
                best = Alignment(template, warped, homography, inliers)
    return best
