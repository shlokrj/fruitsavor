import unittest
import numpy as np
from PIL import Image, ImageDraw
from fruitsavor.vision import analyze


class VisionTests(unittest.TestCase):
    def test_blank_background_abstains(self):
        result = analyze(Image.new('RGB', (128, 128), 'white'))
        self.assertFalse(result.mask.any())
        self.assertEqual(result.features, {})
        self.assertIn('no_foreground_found', result.warnings)

    def test_known_foreground_geometry_and_exclusive_ratios(self):
        image = Image.new('RGB', (128, 128), 'white')
        ImageDraw.Draw(image).ellipse((30, 20, 95, 110), fill=(240, 220, 20))
        truth = np.any(np.asarray(image) != 255, axis=2)
        result = analyze(image)
        iou = (result.mask & truth).sum() / (result.mask | truth).sum()
        self.assertGreater(iou, 0.95)
        ratios = [value for key, value in result.features.items() if key.endswith('_ratio')]
        self.assertAlmostEqual(sum(ratios), 1)
        self.assertGreater(result.features['yellow_like_ratio'], 0.95)
        second = analyze(image)
        np.testing.assert_array_equal(result.mask, second.mask)

    def test_dark_foreground_retained(self):
        image = Image.new('RGB', (128, 128), 'white')
        ImageDraw.Draw(image).ellipse((30, 20, 95, 110), fill=(30, 25, 20))
        result = analyze(image)
        self.assertGreater(result.features['black_like_ratio'], 0.95)
        self.assertGreater(result.features['foreground_fraction'], 0.2)

    def test_small_input_rejected(self):
        with self.assertRaises(ValueError):
            analyze(Image.new('RGB', (8, 8)))

    def test_unsuitable_background_flagged(self):
        image = Image.new('RGB', (128, 128), 'blue')
        ImageDraw.Draw(image).ellipse((30, 20, 95, 110), fill='yellow')
        result = analyze(image)
        self.assertIn('background_outside_plain_light_assumption', result.warnings)
        truth = np.asarray(image)[:, :, 0] > 0
        self.assertGreater((result.mask & truth).sum() / (result.mask | truth).sum(), 0.95)

    def test_connected_cast_shadow_is_not_confident_foreground(self):
        image = Image.new('RGB', (160, 120), (235, 235, 235))
        draw = ImageDraw.Draw(image)
        draw.ellipse((40, 25, 105, 100), fill=(240, 215, 20))
        draw.ellipse((80, 72, 145, 112), fill=(125, 125, 125))
        result = analyze(image)
        fruit = np.zeros((120, 160), bool)
        yy, xx = np.ogrid[:120, :160]
        fruit = ((xx - 72.5) / 32.5) ** 2 + ((yy - 62.5) / 37.5) ** 2 <= 1
        shadow_pixels = ((xx - 112.5) / 32.5) ** 2 + ((yy - 92) / 20) ** 2 <= 1
        self.assertGreater((result.mask & fruit).sum() / fruit.sum(), 0.9)
        self.assertLess((result.mask & shadow_pixels).sum() / shadow_pixels.sum(), 0.5)
